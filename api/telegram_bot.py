import os
import re
import asyncio
import io
import csv
import time
import tempfile
import logging
import threading
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger("telegram_bot")

import numpy as np

from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from ltspice import run_netlist, SimulationResult, RawFile

_SENSITIVE_PATTERNS = [
    re.compile(r'[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s]*'),
    re.compile(r'\\\\(?:[^\\\s]+\\)*[^\\\s]*'),
    re.compile(r'/(?:[^/\s]+/)*[^/\s]*'),
]
_SYSTEM_PLACEHOLDER = "[path]"

def _strip_paths(text: str) -> str:
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(_SYSTEM_PLACEHOLDER, text)
    return text


def _normalize_netlist(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" in text.strip():
        return text
    line = text.strip()
    if not line:
        return line
    # Insert newline before '.' that starts a SPICE directive
    line = re.sub(r'(?<=[^\s])\.(?=[a-z])', r'\n.', line, flags=re.IGNORECASE)
    # Insert newline after ')' followed by a SPICE refdes  e.g. ...1k)R1...
    line = re.sub(r'\)(?=[A-Za-z]\d)', r')\n', line)
    # Insert newline between a digit+unit-suffix and a SPICE refdes  e.g. ...1kR1...
    line = re.sub(r'(\d[kKmMuUnNpP])(?=[A-Za-z]\d)', r'\1\n', line)
    # Insert newline between a plain digit and a known SPICE prefix+digit  e.g. ...5R1...
    line = re.sub(r'(\d)(?=[VvRrCcLlDdMmQqJjXxIiEeGgFfHhBbKkSsWwUuZzOoYyTtPp]\d)', r'\1\n', line)
    line = re.sub(r'\n{2,}', '\n', line)
    return line

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_ALLOWED_CHAT_IDS: set = set()

# Per-chat last simulation result for /avg /rms /img
# value: (timestamp, SimulationResult)
_last_results: Dict[int, tuple[float, "SimulationResult"]] = {}
_RESULT_TTL = 5 * 3600  # 5 hours


def _store_result(chat_id: int, result: "SimulationResult"):
    old = _last_results.pop(chat_id, None)
    if old:
        _, old_result = old
        try:
            old_result.cleanup()
        except Exception:
            pass
    _last_results[chat_id] = (time.time(), result)


def _get_last_result(chat_id: int) -> Optional["SimulationResult"]:
    entry = _last_results.get(chat_id)
    if entry:
        _, result = entry
        return result
    return None


def _cleanup_expired_results():
    now = time.time()
    expired = [cid for cid, (ts, _) in _last_results.items() if now - ts > _RESULT_TTL]
    for cid in expired:
        _, result = _last_results.pop(cid)
        try:
            result.cleanup()
        except Exception:
            pass
    if expired:
        logger.info("Cleaned up %d expired results", len(expired))


def _periodic_cleanup(interval: int = 300):
    while True:
        time.sleep(interval)
        try:
            _cleanup_expired_results()
        except Exception:
            pass


def _parse_chat_ids(raw: str) -> set:
    ids: set = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                pass
    return ids


ALLOWED_ENV = os.environ.get("TELEGRAM_ALLOWED_CHATS", "")
if ALLOWED_ENV:
    _ALLOWED_CHAT_IDS = _parse_chat_ids(ALLOWED_ENV)


def is_allowed(chat_id: int) -> bool:
    if not _ALLOWED_CHAT_IDS:
        return True
    return chat_id in _ALLOWED_CHAT_IDS


def _get_var(result: SimulationResult, var: str):
    if result.raw is None:
        return None
    data = result.raw.values.get(var)
    if data is not None:
        return data
    var_lower = var.lower()
    for key in result.raw.values:
        if key.lower() == var_lower:
            return result.raw.values[key]
    return None


def _signal_names(result):
    if result.raw is None:
        return []
    return list(result.raw.values.keys())


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_allowed(chat_id):
        await update.message.reply_text("Unauthorized")
        return
    await update.message.reply_text(
        f"LTspice Bot ready.\n\n"
        f"Your Chat ID: `{chat_id}`\n\n"
        "Send a SPICE netlist to simulate.\n\n"
        "Commands (after simulation):\n"
        "/avg `<var>`  — average value\n"
        "/rms `<var>`  — RMS value\n"
        "/img `<var>`  — plot image\n"
        "/status       — bot status\n"
        "/help         — this message",
        parse_mode="Markdown",
    )


async def _status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_allowed(chat_id):
        await update.message.reply_text("Unauthorized")
        return
    from ltspice import __version__
    has_result = chat_id in _last_results
    await update.message.reply_text(
        f"*LTspice Bot* (v{__version__})\n"
        f"Chat ID: `{chat_id}`\n"
        f"Last result: {'yes' if has_result else 'none'}\n"
        f"Ready to simulate.",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# /avg /rms /img
# ---------------------------------------------------------------------------
def _signal_names(result) -> list:
    if result.raw is None:
        return []
    return sorted(set(v["name"] for v in result.raw.variables if v["name"] != "time"))


async def _avg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_allowed(chat_id):
        await update.message.reply_text("Unauthorized")
        return
    result = _get_last_result(chat_id)
    if result is None:
        await update.message.reply_text("No previous simulation. Send a netlist first.")
        return
    var = " ".join(context.args) if context.args else ""
    if not var:
        sigs = _signal_names(result)
        if not sigs:
            await update.message.reply_text("No signals available.")
            return
        lines = ["*Signal averages:*"]
        for s in sigs:
            d = _get_var(result, s)
            if d is not None:
                lines.append(f"  `{s}` = `{float(np.mean(d)):.6e}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    data = _get_var(result, var)
    if data is None:
        await update.message.reply_text(f"Variable '{var}' not found. Signals: {', '.join(_signal_names(result))}")
        return
    val = float(np.mean(data))
    await update.message.reply_text(f"*avg({var})* = `{val:.6e}`", parse_mode="Markdown")


async def _rms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_allowed(chat_id):
        await update.message.reply_text("Unauthorized")
        return
    result = _get_last_result(chat_id)
    if result is None:
        await update.message.reply_text("No previous simulation. Send a netlist first.")
        return
    var = " ".join(context.args) if context.args else ""
    if not var:
        sigs = _signal_names(result)
        if not sigs:
            await update.message.reply_text("No signals available.")
            return
        lines = ["*Signal RMS:*"]
        for s in sigs:
            d = _get_var(result, s)
            if d is not None:
                lines.append(f"  `{s}` = `{float(np.sqrt(np.mean(d ** 2))):.6e}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    data = _get_var(result, var)
    if data is None:
        await update.message.reply_text(f"Variable '{var}' not found. Signals: {', '.join(_signal_names(result))}")
        return
    val = float(np.sqrt(np.mean(data ** 2)))
    await update.message.reply_text(f"*rms({var})* = `{val:.6e}`", parse_mode="Markdown")


async def _img(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_allowed(chat_id):
        await update.message.reply_text("Unauthorized")
        return
    result = _get_last_result(chat_id)
    if result is None:
        await update.message.reply_text("No previous simulation. Send a netlist first.")
        return
    var = " ".join(context.args) if context.args else ""
    sigs = _signal_names(result)
    if not sigs:
        await update.message.reply_text("No signals available.")
        return
    time = result.raw.time
    if time is None:
        await update.message.reply_text("No time data (OP simulation has no time axis)")
        return

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if var:
        data = _get_var(result, var)
        if data is None:
            await update.message.reply_text(f"Variable '{var}' not found. Signals: {', '.join(sigs)}")
            return
        vars_to_plot = [(var, data)]
    else:
        vars_to_plot = [(s, _get_var(result, s)) for s in sigs if _get_var(result, s) is not None]

    media_group = []
    for name, data in vars_to_plot:
        try:
            fig = plt.figure(figsize=(8, 4))
            fig.add_axes([0.1, 0.12, 0.85, 0.82])
            ax = fig.gca()
            ax.plot(time, data, "b", linewidth=0.8)
            ax.set_ylabel(name, fontsize=10)
            ax.tick_params(labelsize=8)

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=120)
            buf.seek(0)
            plt.close(fig)
            media_group.append(buf)
        except Exception as e:
            logger.error(f"Plot generation failed for {name}: {e}")
            plt.close("all")

    if not media_group:
        await update.message.reply_text("Failed to generate plots")
        return

    from telegram import InputMediaPhoto, InputFile

    total = len(vars_to_plot)
    if total == 1:
        await update.message.reply_photo(photo=media_group[0], caption=f"Plot: {vars_to_plot[0][0]}")
        return

    batches = []
    remaining = total
    while remaining > 0:
        batch_size = min(10, remaining)
        if remaining - batch_size == 1:
            batch_size = 9
        batches.append(batch_size)
        remaining -= batch_size

    idx = 0
    for batch_size in batches:
        batch = []
        for j in range(batch_size):
            i = idx + j
            batch.append(
                InputMediaPhoto(
                    media=InputFile(media_group[i], filename=f"plot_{i}.png", attach=True),
                    caption=f"Plot: {vars_to_plot[i][0]}",
                )
            )
        await update.message.reply_media_group(media=batch)
        idx += batch_size


# ---------------------------------------------------------------------------
# Netlist handler
# ---------------------------------------------------------------------------
async def _handle_netlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_allowed(chat_id):
        await update.message.reply_text("Unauthorized")
        return

    text = update.message.text.strip()

    if not text or text.startswith("/"):
        return

    text = _normalize_netlist(text)

    await update.message.reply_text("Running simulation...")

    try:
        result = run_netlist(text, filename=f"tg_{chat_id}_{update.message.message_id}")
    except Exception as e:
        await update.message.reply_text(f"Error: {_strip_paths(str(e))}")
        return

    if result is None:
        await update.message.reply_text("No result returned")
        return

    _store_result(chat_id, result)
    await _send_result_summary(update, result)


async def _error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Bot error: {context.error}")
    import traceback
    logger.error(traceback.format_exc())
    if update and update.effective_message:
        await update.effective_message.reply_text(f"Error: {context.error}")


# ---------------------------------------------------------------------------
# Helpers — send result to chat (shared by netlist & raw handlers)
# ---------------------------------------------------------------------------
async def _send_result_summary(update_or_chat, result, file_caption: str = ""):
    if hasattr(update_or_chat, "message"):
        chat_id = update_or_chat.effective_chat.id
        send = update_or_chat.message.reply_text
        send_doc = update_or_chat.message.reply_document
        send_photo = update_or_chat.message.reply_photo
    else:
        chat_id = update_or_chat
        bot = get_bot()
        send = lambda t: bot.send_message(chat_id=chat_id, text=t)
        send_doc = lambda **kw: bot.send_document(chat_id=chat_id, **kw)
        send_photo = lambda **kw: bot.send_photo(chat_id=chat_id, **kw)

    prefix = f"*{file_caption}*\n\n" if file_caption else ""

    info = []
    if result.raw:
        info.append(f"*{result.raw.plotname}* — {result.raw.num_variables} vars, {result.raw.num_points} pts")
        if result.raw.time is not None:
            info.append(f"Time: `{result.raw.time[0]:.4e}` → `{result.raw.time[-1]:.4e}`")
    info.append(f"Solver: `{result.solver}`, Method: `{result.method}`, Temp: `{result.temp}°C`, Elapsed: `{result.elapsed_time:.3f}s`")

    body = "\n".join(info)

    if result.measurements:
        m_lines = ["\n*Measurements*"]
        for name, val in result.measurements.items():
            m_lines.append(f"  `{name}` = `{val:.6e}`")
        body += "\n".join(m_lines)

    if result.raw:
        sig_lines = ["\n*Signals*"]
        for var in result.raw.variables:
            name = var["name"]
            if name == "time":
                continue
            data = result.raw.values.get(name)
            if data is not None and len(data) > 0:
                peak = float(np.max(np.abs(data)))
                mean = float(np.mean(data))
                rms_val = float(np.sqrt(np.mean(data ** 2)))
                sig_lines.append(f"  `{name}` — peak `{peak:.4e}`, mean `{mean:.4e}`, rms `{rms_val:.4e}`")
        body += "\n".join(sig_lines)

    if len(body) > 3900:
        body = body[:3900] + "\n\n…(truncated)"

    await send(f"{prefix}{body}", parse_mode="Markdown")

    if result.measurements:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Measurement", "Value"])
        for name, val in result.measurements.items():
            w.writerow([name, f"{val:.6e}"])
        csv_bytes = buf.getvalue().encode()
        await send_doc(
            document=io.BytesIO(csv_bytes),
            filename="measurements.csv",
            caption=f"Measurements ({len(result.measurements)} items)",
        )

    if result.raw and result.raw.time is not None:
        for var in result.raw.variables:
            if var["name"] == "time":
                continue
            try:
                fft_data = result.fft(var["name"])
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["Freq (Hz)", "Mag", "Phase (deg)"])
                for f, m, p in zip(fft_data["freq"][:500], fft_data["mag"][:500], fft_data["phase"][:500]):
                    w.writerow([f"{f:.4e}", f"{m:.6e}", f"{p:.4f}"])
                csv_bytes = buf.getvalue().encode()
                await send_doc(
                    document=io.BytesIO(csv_bytes),
                    filename=f"{var['name']}_fft.csv",
                    caption=f"FFT: {var['name']} ({len(fft_data['freq'])} bins)",
                )
            except Exception:
                pass
            break


# ---------------------------------------------------------------------------
# Document handler — .txt .cir .net .raw files
# ---------------------------------------------------------------------------
_VALID_EXTENSIONS = {".txt", ".cir", ".net", ".sp", ".asc", ".raw"}


async def _handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_allowed(chat_id):
        await update.message.reply_text("Unauthorized")
        return

    doc = update.message.document
    if doc is None:
        return

    ext = os.path.splitext(doc.file_name or "")[1].lower()
    if ext not in _VALID_EXTENSIONS:
        await update.message.reply_text(
            f"Unsupported file type `{ext}`. Send `.txt`, `.cir`, `.net`, `.sp`, `.asc`, or `.raw`.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("Reading file...")

    try:
        file = await doc.get_file()
        content_bytes = await file.download_as_bytearray()
    except Exception as e:
        await update.message.reply_text(f"Error reading file: {_strip_paths(str(e))}")
        return

    # Handle .raw files directly — parse binary, no simulation needed
    if ext == ".raw":
        try:
            import tempfile
            tmp = Path(tempfile.mkdtemp()) / (doc.file_name or "data.raw")
            tmp.write_bytes(content_bytes)
            raw = RawFile(tmp)
            result = SimulationResult(raw=raw, netlist_text=f"Loaded from {doc.file_name}")
            tmp.unlink(missing_ok=True)
        except Exception as e:
            await update.message.reply_text(f"Error parsing .raw: {_strip_paths(str(e))}")
            return

        _store_result(chat_id, result)
        await _send_result_summary(update, result, file_caption=doc.file_name or "data.raw")
        return

    # Text-based netlist files
    try:
        text = content_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        await update.message.reply_text(f"Error decoding file: {_strip_paths(str(e))}")
        return

    if not text.strip():
        await update.message.reply_text("File is empty")
        return

    text = _normalize_netlist(text)

    await update.message.reply_text(f"Running simulation from `{doc.file_name}`...", parse_mode="Markdown")

    try:
        result = run_netlist(text, filename=f"tg_{chat_id}_{update.message.message_id}")
    except Exception as e:
        await update.message.reply_text(f"Error: {_strip_paths(str(e))}")
        return

    if result is None:
        await update.message.reply_text("No result returned")
        return

    _store_result(chat_id, result)
    await _send_result_summary(update, result, file_caption=doc.file_name or "")


# ---------------------------------------------------------------------------
# Sender helpers — call from API endpoints
# ---------------------------------------------------------------------------
_bot_app: Optional[Application] = None
_bot_instance: Optional[Bot] = None


def get_bot() -> Optional[Bot]:
    global _bot_instance
    return _bot_instance


async def send_result(chat_id: int, result, include_fft: bool = True):
    bot = get_bot()
    if bot is None or not is_allowed(chat_id):
        return

    _store_result(chat_id, result)
    await _send_result_summary(chat_id, result)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
def start_bot():
    global _bot_app, _bot_instance
    if not BOT_TOKEN:
        return

    t = threading.Thread(target=_periodic_cleanup, args=(300,), daemon=True)
    t.start()

    while True:
        try:
            _bot_app = Application.builder().token(BOT_TOKEN).build()
            _bot_instance = _bot_app.bot

            _bot_app.add_handler(CommandHandler("start", _start))
            _bot_app.add_handler(CommandHandler("help", _start))
            _bot_app.add_handler(CommandHandler("status", _status))
            _bot_app.add_handler(CommandHandler("avg", _avg))
            _bot_app.add_handler(CommandHandler("rms", _rms))
            _bot_app.add_handler(CommandHandler("img", _img))
            _bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_netlist))
            _bot_app.add_handler(MessageHandler(filters.Document.FileExtension("txt") | filters.Document.FileExtension("cir") | filters.Document.FileExtension("net") | filters.Document.FileExtension("sp") | filters.Document.FileExtension("asc") | filters.Document.FileExtension("raw"), _handle_document))
            _bot_app.add_error_handler(_error_handler)

            logger.info("Telegram bot starting polling...")
            _bot_app.run_polling()
        except Exception as e:
            logger.error(f"Telegram bot crashed: {e}. Restarting in 10s...")
            _bot_instance = None
            time.sleep(10)
