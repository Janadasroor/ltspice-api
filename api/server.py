import uuid
import os
import re
import math
import shutil
import tempfile
import threading
from pathlib import Path, PureWindowsPath
from typing import Optional, Dict, Any, List, Set
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel, Field

from ltspice import (
    Circuit, Netlist,
    run_netlist as _run_netlist,
    SimulationResult,
)

from . import telegram_bot

# Ephemeral work directory — no local paths exposed to clients
WORK_DIR = Path(tempfile.mkdtemp(prefix="ltspice_api_"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

# Persistent library storage for uploaded .lib/.sub files
LIB_DIR = Path(tempfile.mkdtemp(prefix="ltspice_libs_"))
LIB_DIR.mkdir(parents=True, exist_ok=True)

# Persistent symbol storage for uploaded .asy files
SYM_DIR = Path(tempfile.mkdtemp(prefix="ltspice_sym_"))
SYM_DIR.mkdir(parents=True, exist_ok=True)

# LTspice help extracted HTML files
HELP_DIR = Path(r"C:\Users\js\AppData\Local\Temp\opencode\ltspice_help")
# Persistent custom documentation / agent memory
CUSTOM_DOC_DIR = Path(os.path.expanduser("~")) / ".gemini" / "ltspice_api" / "custom_docs"
CUSTOM_DOC_DIR.mkdir(parents=True, exist_ok=True)

_HELP_INDEX: Dict[str, Dict[str, Any]] = {}  # filename -> {title, content, type}


def _build_help_index():
    _HELP_INDEX.clear()
    # 1. Official Help
    html_dir = HELP_DIR / "html"
    if html_dir.exists():
        for f in sorted(html_dir.iterdir()):
            if f.suffix.lower() in (".htm", ".html"):
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    title = ""
                    m = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE)
                    if m:
                        title = m.group(1).strip()
                    # Strip HTML tags for searchable text
                    plain = re.sub(r"<[^>]+>", " ", text)
                    plain = re.sub(r"\s+", " ", plain).strip()
                    _HELP_INDEX[f.stem] = {
                        "title": title or f.stem,
                        "content": plain,
                        "filename": f.name,
                        "type": "official"
                    }
                except Exception:
                    pass

    # 2. Custom docs (Agent Memory)
    if CUSTOM_DOC_DIR.exists():
        for f in sorted(CUSTOM_DOC_DIR.iterdir()):
            if f.suffix.lower() == ".md":
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    title = f.stem
                    lines = content.splitlines()
                    if lines and lines[0].startswith("#"):
                        title = lines[0].lstrip("#").strip()
                    _HELP_INDEX[f.stem] = {
                        "title": title,
                        "content": content,
                        "filename": f.name,
                        "type": "custom"
                    }
                except Exception:
                    pass


_build_help_index()

# Track temp dirs we create for cleanup
_temp_dirs: Set[str] = set()

results_store: Dict[str, SimulationResult] = {}
netlist_store: Dict[str, str] = {}

# ---------------------------------------------------------------------------
# Path sanitisation — strip any local filesystem paths from API responses
# ---------------------------------------------------------------------------
# Patterns to scrub from text returned to clients
_SENSITIVE_PATTERNS = [
    # Windows drive-letter paths  e.g. C:\Users\js\...
    re.compile(r'[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s]*'),
    # UNC paths  e.g. \\server\share\...
    re.compile(r'\\\\(?:[^\\\s]+\\)*[^\\\s]*'),
    # Unix-style absolute paths that might appear
    re.compile(r'/(?:[^/\s]+/)*[^/\s]*'),
]

_SYSTEM_PLACEHOLDER = "[path]"


def _strip_paths(text: str) -> str:
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(_SYSTEM_PLACEHOLDER, text)
    return text


def _sanitize_list(items: List[str]) -> List[str]:
    return [_strip_paths(i) for i in items]


def _sanitize_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("errors", "warnings", "files_loaded"):
        if key in d and isinstance(d[key], list):
            d[key] = _sanitize_list(d[key])
    for key in ("log_text",):
        if key in d and isinstance(d[key], str):
            d[key] = _strip_paths(d[key])
    return d


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class ComponentDef(BaseModel):
    type: str = Field(description="Component type: R, C, L, D, V, I, NMOS, PMOS, NPN, PNP, NJF, PJF, E, G, F, H, BV, BI, K, TLINE, SW, SUBCKT")
    name: str = Field(description="Component reference designator")
    nodes: List[str] = Field(description="Connection nodes (2-5 depending on type)")
    value: str = Field(default="", description="Value, model name, or expression")
    params: Dict[str, str] = Field(default={}, description="Extra parameters (e.g. L=1u for TLINE)")

class CircuitDef(BaseModel):
    title: str = Field(default="Circuit", description="Circuit title")
    components: List[ComponentDef] = Field(default=[], description="List of components")
    controls: List[str] = Field(default=[], description="SPICE directives (.tran .ac .op etc)")
    params: Dict[str, str] = Field(default={}, description=".param definitions")
    models: List[str] = Field(default=[], description=".model definitions")
    options: Dict[str, Optional[str]] = Field(default={}, description=".options")
    measurements: List[str] = Field(default=[], description=".meas directives")
    analysis: Optional[str] = Field(default=None, description="Override auto-detection: 'tran 1m', 'ac dec 100 1 1meg', 'op'")
    telegram_chat_id: Optional[int] = Field(default=None, description="Telegram chat ID to send results to")

class SimulateRequest(BaseModel):
    netlist: str = Field(description="Full SPICE netlist text")
    timeout: Optional[int] = Field(default=None, description="Timeout in seconds")
    telegram_chat_id: Optional[int] = Field(default=None, description="Telegram chat ID to send results to")

class SimulateResponse(BaseModel):
    id: str
    status: str
    message: str

class LibUploadRequest(BaseModel):
    filename: str = Field(description="Library filename (e.g. 'mylib.lib')")
    content: str = Field(description="File content")

class LibUploadResponse(BaseModel):
    filename: str
    path: str
    message: str

class SymUploadRequest(BaseModel):
    filename: str = Field(description="Symbol filename (e.g. 'my_opamp.asy')")
    content: str = Field(description="File content")

class SymUploadResponse(BaseModel):
    filename: str
    path: str
    message: str

class HelpTopicRequest(BaseModel):
    name: str = Field(description="Topic name (slug, e.g. 'lesson_learned_1')")
    content: str = Field(description="Markdown content (should include a # Title)")

# ---------------------------------------------------------------------------
# Component type → Circuit method mapping
# ---------------------------------------------------------------------------
_TYPES = {
    "R": "resistor", "C": "capacitor", "L": "inductor",
    "D": "diode",
    "NMOS": "nmos", "PMOS": "pmos",
    "NJF": "njfet", "PJF": "pjfet",
    "NPN": "npn", "PNP": "pnp",
    "V": "v", "I": "i",
    "E": "e", "G": "g", "F": "f", "H": "h",
    "BV": "bv", "BI": "bi",
    "K": "k", "TLINE": "tline", "SW": "sw",
    "SUBCKT": "sub",
}


def _build_circuit(defn: CircuitDef) -> Circuit:
    cir = Circuit(defn.title)
    for cd in defn.components:
        ctype = cd.type.upper()
        method = _TYPES.get(ctype)
        if method is None:
            raise HTTPException(400, f"Unknown component type: {ctype}")
        nodes = cd.nodes
        val = cd.value
        cir_method = getattr(cir, method)
        if ctype == "SUBCKT":
            cir.sub(cd.name, nodes, val, **cd.params)
        elif ctype in ("R", "C", "L"):
            if len(nodes) < 2:
                raise HTTPException(400, f"{ctype} needs 2 nodes")
            cir_method(cd.name, nodes[0], nodes[1], val, **cd.params)
        elif ctype == "D":
            if len(nodes) < 2:
                raise HTTPException(400, "Diode needs 2 nodes")
            cir_method(cd.name, nodes[0], nodes[1], val, **cd.params)
        elif ctype in ("NMOS", "PMOS"):
            if len(nodes) < 3:
                raise HTTPException(400, f"{ctype} needs 3+ nodes")
            cir_method(cd.name, nodes[0], nodes[1], nodes[2],
                       nodes[3] if len(nodes) > 3 else None, val, **cd.params)
        elif ctype in ("NJF", "PJF", "NPN", "PNP"):
            if len(nodes) < 3:
                raise HTTPException(400, f"{ctype} needs 3 nodes")
            cir_method(cd.name, nodes[0], nodes[1], nodes[2], val, **cd.params)
        elif ctype in ("V", "I"):
            if len(nodes) < 2:
                raise HTTPException(400, f"{ctype} needs 2 nodes")
            cir_method(cd.name, nodes[0], nodes[1], val, **cd.params)
        elif ctype in ("E", "G"):
            if len(nodes) < 4:
                raise HTTPException(400, f"{ctype} needs 4 nodes")
            cir_method(cd.name, nodes[0], nodes[1], nodes[2], nodes[3], val, **cd.params)
        elif ctype in ("F", "H"):
            if len(nodes) < 3:
                raise HTTPException(400, f"{ctype} needs 3 nodes (out+ out- sense_vsrc)")
            cir_method(cd.name, nodes[0], nodes[1], nodes[2], val, **cd.params)
        elif ctype in ("BV", "BI"):
            if len(nodes) < 2:
                raise HTTPException(400, f"{ctype} needs 2 nodes")
            cir_method(cd.name, nodes[0], nodes[1], val, **cd.params)
        elif ctype == "K":
            if len(nodes) < 2:
                raise HTTPException(400, "K needs 2 nodes (inductor names)")
            cir_method(cd.name, nodes[0], nodes[1], val, **cd.params)
        elif ctype == "TLINE":
            if len(nodes) < 4:
                raise HTTPException(400, "TLINE needs 4 nodes")
            td = cd.params.get("TD", cd.params.get("td", ""))
            cir_method(cd.name, nodes[0], nodes[1], nodes[2], nodes[3], val, td, **cd.params)
        elif ctype == "SW":
            if len(nodes) < 4:
                raise HTTPException(400, "SW needs 4 nodes")
            cir_method(cd.name, nodes[0], nodes[1], nodes[2], nodes[3], val, **cd.params)

    for k, v in defn.params.items():
        cir.param(k, v)
    for m in defn.models:
        cir.model(m)
    for k, v in defn.options.items():
        cir.option(k, v)
    for m in defn.measurements:
        cir.control(m if m.startswith(".meas") else f".meas {m}")

    if defn.analysis:
        a = defn.analysis.strip()
        if a.startswith("tran"):
            parts = a.split()
            cir.tran(*parts[1:]) if len(parts) > 1 else cir.tran("1m")
        else:
            cir.control(a)

    return cir


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
_bot_thread: Optional[threading.Thread] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_thread
    if telegram_bot.BOT_TOKEN:
        _bot_thread = threading.Thread(target=telegram_bot.start_bot, daemon=True)
        _bot_thread.start()
    yield
    # shutdown handled by daemon thread


app = FastAPI(
    title="LTspice REST API",
    version="0.2.0",
    lifespan=lifespan,
    description="Production-grade REST API for LTspice circuit simulation",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/help")
async def help():
    return {
        "endpoints": {
            "GET /health": "Server status",
            "GET /help": "This help message",
            "POST /netlist": "Generate SPICE netlist from component definition",
            "POST /simulate": "Run simulation from netlist text",
            "POST /circuit": "Run simulation from component definition",
            "GET /results/{sim_id}": "Simulation metadata",
            "GET /results/{sim_id}/summary": "Formatted simulation summary",
            "GET /results/{sim_id}/data?var=X": "Signal data for variable X",
            "GET /results/{sim_id}/fft?var=X": "FFT analysis for variable X",
            "GET /results/{sim_id}/measurements": ".meas measurement values",
            "GET /results/{sim_id}/log": "Raw LTspice log",
            "GET /results/{sim_id}/netlist": "Original netlist",
            "DELETE /results/{sim_id}": "Free server resources",
            "GET /telegram/status": "Telegram bot status",
        }
    }


@app.post("/library")
async def upload_library(req: LibUploadRequest):
    name = req.filename.strip()
    # Sanitize — allow only safe filenames
    if not re.match(r'^[\w.-]+$', name):
        raise HTTPException(400, f"Invalid filename: {name}")
    path = LIB_DIR / name
    path.write_text(req.content)
    return LibUploadResponse(filename=name, path=_strip_paths(str(path)), message="Library saved")


@app.post("/symbol")
async def upload_symbol(req: SymUploadRequest):
    name = req.filename.strip()
    if not re.match(r'^[\w.-]+$', name):
        raise HTTPException(400, f"Invalid filename: {name}")
    path = SYM_DIR / name
    path.write_text(req.content)
    return SymUploadResponse(filename=name, path=_strip_paths(str(path)), message="Symbol saved")


@app.get("/symbol/{filename}", response_class=PlainTextResponse)
async def get_symbol(filename: str):
    if not re.match(r'^[\w.-]+$', filename):
        raise HTTPException(400, f"Invalid filename: {filename}")
    path = SYM_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"Symbol '{filename}' not found")
    return path.read_text()


@app.post("/netlist", response_class=PlainTextResponse)
async def netlist_from_circuit(defn: CircuitDef):
    cir = _build_circuit(defn)
    return str(cir)


@app.post("/simulate")
async def simulate(req: SimulateRequest):
    sim_id = uuid.uuid4().hex[:12]
    netlist_store[sim_id] = req.netlist
    # Copy uploaded libraries into work dir so LTspice can find them
    for f in LIB_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, WORK_DIR / f.name)
    try:
        result = _run_netlist(
            req.netlist,
            work_dir=str(WORK_DIR),
            filename=sim_id,
            timeout=req.timeout,
        )
    except Exception as e:
        results_store[sim_id] = SimulationResult(
            netlist_text=req.netlist,
            returncode=-1,
            stderr_text=str(e),
        )
        return SimulateResponse(id=sim_id, status="error", message=_strip_paths(str(e)))

    if result is None:
        return SimulateResponse(id=sim_id, status="error", message="No result returned")

    results_store[sim_id] = result
    status = "ok" if result.successful else "error"
    msg = "Simulation completed" if result.successful else f"Simulation failed (code {result.returncode})"

    if req.telegram_chat_id and telegram_bot.get_bot():
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(telegram_bot.send_result(req.telegram_chat_id, result))
        except RuntimeError:
            asyncio.run(telegram_bot.send_result(req.telegram_chat_id, result))

    return SimulateResponse(id=sim_id, status=status, message=msg)


@app.post("/circuit")
async def simulate_circuit(defn: CircuitDef):
    sim_id = uuid.uuid4().hex[:12]
    try:
        cir = _build_circuit(defn)
        result = cir.run(work_dir=str(WORK_DIR), filename=sim_id)
    except Exception as e:
        results_store[sim_id] = SimulationResult(
            netlist_text=str(_build_circuit(defn)) if defn.components else "",
            returncode=-1,
            stderr_text=str(e),
        )
        return SimulateResponse(id=sim_id, status="error", message=_strip_paths(str(e)))

    results_store[sim_id] = result
    status = "ok" if result.successful else "error"
    msg = "Simulation completed" if result.successful else f"Simulation failed (code {result.returncode})"

    if defn.telegram_chat_id and telegram_bot.get_bot():
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(telegram_bot.send_result(defn.telegram_chat_id, result))
        except RuntimeError:
            asyncio.run(telegram_bot.send_result(defn.telegram_chat_id, result))

    return SimulateResponse(id=sim_id, status=status, message=msg)


def _sanitize_numeric(v: Any) -> Any:
    """Replace inf/nan with None so JSON serialization doesn't 500."""
    if isinstance(v, float):
        if math.isinf(v) or math.isnan(v):
            return None
    return v


def _sanitize_list_numeric(lst: list) -> list:
    return [_sanitize_numeric(x) for x in lst]


def _get_result(sim_id: str) -> SimulationResult:
    r = results_store.get(sim_id)
    if r is None:
        raise HTTPException(404, f"Simulation {sim_id} not found")
    return r


def _sanitized_result(sim_id: str, include_data: bool = False) -> Dict[str, Any]:
    r = _get_result(sim_id)
    d = r.to_dict(include_data=include_data)
    d["files_loaded"] = _sanitize_list(r.files_loaded)
    return _sanitize_dict(d)


def _sanitized_summary(sim_id: str) -> str:
    r = _get_result(sim_id)
    text = r.summary
    return _strip_paths(text)


def _sanitized_log(sim_id: str) -> str:
    r = _get_result(sim_id)
    return _strip_paths(r.log_text)


@app.get("/results/{sim_id}")
async def get_results(sim_id: str, include_data: bool = Query(False)):
    return JSONResponse(content=_sanitized_result(sim_id, include_data=include_data))


@app.get("/results/{sim_id}/summary", response_class=PlainTextResponse)
async def get_summary(sim_id: str):
    return _sanitized_summary(sim_id)


@app.get("/results/{sim_id}/data")
async def get_data(sim_id: str, var: Optional[str] = Query(None)):
    r = _get_result(sim_id)
    if r.raw is None:
        raise HTTPException(400, "No raw data available")
    if var:
        data = r.raw.values.get(var)
        if data is None:
            raise HTTPException(404, f"Variable '{var}' not found")
        return {
            "variable": var,
            "time": _sanitize_list_numeric(r.raw.time.tolist()) if r.raw.time is not None else None,
            "data": _sanitize_list_numeric(data.tolist()),
            "unit": next((v.get("unit", "") for v in r.raw.variables if v["name"] == var), ""),
        }
    result: Dict[str, Any] = {}
    if r.raw.time is not None:
        result["time"] = _sanitize_list_numeric(r.raw.time.tolist())
    for name, vals in r.raw.values.items():
        result[name] = _sanitize_list_numeric(vals.tolist())
    return result


@app.get("/results/{sim_id}/fft")
async def get_fft(sim_id: str, var: str = Query(...), window: str = Query("hanning")):
    r = _get_result(sim_id)
    try:
        fft_data = r.fft(var, window=window)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {
        "variable": var,
        "freq": _sanitize_list_numeric(fft_data["freq"].tolist()),
        "mag": _sanitize_list_numeric(fft_data["mag"].tolist()),
        "phase": _sanitize_list_numeric(fft_data["phase"].tolist()),
        "fs": fft_data["fs"],
        "n": fft_data["n"],
    }


@app.get("/results/{sim_id}/measurements")
async def get_measurements(sim_id: str):
    r = _get_result(sim_id)
    return {"measurements": dict(r.measurements)}


@app.get("/results/{sim_id}/log", response_class=PlainTextResponse)
async def get_log(sim_id: str):
    return _sanitized_log(sim_id)


@app.get("/results/{sim_id}/netlist", response_class=PlainTextResponse)
async def get_netlist(sim_id: str):
    r = _get_result(sim_id)
    if r.netlist_text:
        return r.netlist_text
    nl = netlist_store.get(sim_id)
    if nl:
        return nl
    raise HTTPException(404, f"No netlist found for {sim_id}")


@app.get("/telegram/status")
async def telegram_status():
    bot = telegram_bot.get_bot()
    if not bot:
        return {"enabled": False, "token_set": bool(telegram_bot.BOT_TOKEN)}
    return {"enabled": True, "allowed_chats": list(telegram_bot._ALLOWED_CHAT_IDS)}


@app.delete("/results/{sim_id}")
async def delete_result(sim_id: str):
    results_store.pop(sim_id, None)
    netlist_store.pop(sim_id, None)
    # Clean up work files
    for f in WORK_DIR.glob(f"{sim_id}.*"):
        f.unlink(missing_ok=True)
    return {"status": "deleted", "id": sim_id}


# ---------------------------------------------------------------------------
# Help endpoints
# ---------------------------------------------------------------------------


@app.get("/help/search")
async def help_search(q: str = Query(..., description="Search query")):
    q = q.lower()
    results = []
    for stem, info in _HELP_INDEX.items():
        searchable = f"{info['title']} {stem} {info['content']}".lower()
        if q in searchable:
            # Create a snippet around the match
            idx = info["content"].lower().find(q)
            start = max(0, idx - 60)
            end = min(len(info["content"]), idx + len(q) + 120)
            snippet = info["content"][start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(info["content"]):
                snippet = snippet + "..."
            results.append({
                "topic": stem,
                "title": info["title"],
                "type": info.get("type", "official"),
                "snippet": snippet,
            })
    return {"query": q, "count": len(results), "results": results}


@app.get("/help/topic")
async def help_topic(name: str = Query(..., description="Topic name (filename without extension)")):
    info = _HELP_INDEX.get(name)
    if info is None:
        raise HTTPException(404, f"Help topic '{name}' not found")
    
    if info.get("type") == "custom":
        path = CUSTOM_DOC_DIR / info["filename"]
    else:
        path = HELP_DIR / "html" / info["filename"]
        
    if not path.exists():
        raise HTTPException(404, f"Help file not found")
    return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"))


@app.post("/help/topic")
async def save_help_topic(req: HelpTopicRequest):
    # Sanitize name - alphanumeric and underscores/dashes only
    name = re.sub(r'[^\w\-]', '_', req.name).strip("_")
    if not name:
        raise HTTPException(400, "Invalid topic name")
    
    filename = f"{name}.md"
    path = CUSTOM_DOC_DIR / filename
    path.write_text(req.content, encoding="utf-8")
    
    # Refresh index
    _build_help_index()
    
    return {"status": "saved", "topic": name, "filename": filename}
