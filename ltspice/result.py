import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Union, Any
from .raw_read import RawFile


class SimulationResult:
    def __init__(
        self,
        raw: Optional[RawFile] = None,
        log_path: Optional[Path] = None,
        netlist_text: str = "",
        netlist_path: Optional[Path] = None,
        stdout_text: str = "",
        stderr_text: str = "",
        returncode: int = 0,
    ):
        self.raw = raw
        if isinstance(log_path, str):
            log_path = Path(log_path)
        self.log_path = Path(log_path) if log_path else None
        self.log_text: str = ""
        self.measurements: Dict[str, float] = {}
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.solver: str = "Normal"
        self.tnom: float = 27.0
        self.temp: float = 27.0
        self.method: str = "trap"
        self.elapsed_time: float = 0.0
        self.start_time: str = ""
        self.files_loaded: List[str] = []
        self.netlist_text: str = netlist_text
        self.netlist_path: Optional[Path] = Path(netlist_path) if isinstance(netlist_path, str) else netlist_path
        self.stdout_text: str = stdout_text
        self.stderr_text: str = stderr_text
        self.returncode: int = returncode
        self.successful: bool = returncode == 0

        self._parse_output()
        if self.log_path and self.log_path.exists():
            self._parse_log()

    def _parse_output(self):
        for text, dest in [(self.stderr_text, self.errors),
                           (self.stdout_text, self.warnings)]:
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if "error" in line.lower():
                    self.errors.append(line)
                elif "warning" in line.lower() or "abort" in line.lower():
                    self.warnings.append(line)

    def _parse_log(self):
        self.log_text = self.log_path.read_text(encoding="latin-1", errors="replace")
        # print(f"Parsing log: {self.log_path}, len={len(self.log_text)}")
        in_files_loaded = False
        for line in self.log_text.split("\n"):
            line_raw = line  # keep original for error parsing
            line = line.strip()
            if not line:
                continue
            # print(f"Processing line: {line}")
            # Extract error messages from log (e.g. "broken.net(2): This sub-circuit name is not defined.")
            if (("):" in line or line.startswith("[path]") or line.startswith("/")) and
                any(kw in line.lower() for kw in ("error", "not defined", "invalid", "unknown", "syntax", "failed", "can't", "expected", "unexpected"))):
                self.errors.append(line.strip())
            elif "warning" in line.lower() and "=" not in line:
                self.warnings.append(line.strip())
        for line in self.log_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("solver ="):
                self.solver = line.split("=", 1)[1].strip()
            elif line.startswith("tnom ="):
                try:
                    self.tnom = float(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("temp ="):
                try:
                    self.temp = float(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("method ="):
                self.method = line.split("=", 1)[1].strip()
            elif line.startswith("Total elapsed time:"):
                val = line.split(":")[1].strip().replace("seconds", "").strip()
                val = val.split()[0] if val.split() else "0"
                try:
                    self.elapsed_time = float(val)
                except ValueError:
                    self.elapsed_time = 0.0
            elif line.startswith("Start Time:"):
                self.start_time = line.split(":", 1)[1].strip()
            elif line.startswith("Files loaded:"):
                in_files_loaded = True
            elif in_files_loaded and (line.startswith("C:") or line.startswith("\\")):
                self.files_loaded.append(line.strip())
            else:
                in_files_loaded = False

            # Parse .meas results from log
            if ".meas" in line.lower() or ":" in line or "=" in line:
                # Skip simulation metadata lines
                if any(line.startswith(x) for x in ("solver =", "tnom =", "temp =", "method =", "Total elapsed time:", "Start Time:", "Circuit:", "LTspice")):
                    continue
                self._try_parse_meas_line(line)

        # Also try parsing multi-line .meas results format
        self._parse_meas_block()

    def _try_parse_meas_line(self, line: str):
        # Skip lines that are obviously not measurements
        if line.startswith(".") or line.startswith("Files loaded:"):
            return

        # print(f"Trying to parse meas line: {line}")
        if ":" in line and "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 2:
                name = parts[0].strip().rstrip(":")
                try:
                    self.measurements[name] = float(parts[1].strip())
                    # print(f"Found meas (tab): {name}={self.measurements[name]}")
                    return
                except ValueError:
                    pass
        
        # Handle "vpeak: MAX(V(out))=0.862229824066 FROM 0 TO 0.002"
        if ":" in line and "=" in line:
            parts = line.split(":", 1)
            name = parts[0].strip()
            # Basic validation: name should not contain spaces if it's a meas name
            if " " in name:
                return
            rest = parts[1].strip()
            if "=" in rest:
                val_part = rest.split("=", 1)[1].strip()
                try:
                    self.measurements[name] = float(val_part.split()[0])
                    return
                except (ValueError, IndexError):
                    pass

        if "=" in line and not line.startswith("."):
            for sep in [":\t", ":\t\t", "="]:
                if sep in line:
                    parts = line.split(sep, 1)
                    name = parts[0].strip()
                    if " " in name or not name:
                        continue
                    val_str = parts[1].strip()
                    try:
                        self.measurements[name] = float(val_str.split()[0])
                        return
                    except (ValueError, IndexError):
                        pass
                    break

    def _parse_meas_block(self):
        in_meas = False
        for line in self.log_text.split("\n"):
            if "Measurement:" in line or "measurement" in line.lower():
                in_meas = True
                continue
            if in_meas and line.strip() and ":" in line and "\t" in line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    name = parts[0].strip().rstrip(":")
                    try:
                        self.measurements[name] = float(parts[1].strip())
                    except ValueError:
                        pass

    @property
    def summary(self) -> str:
        lines = ["# Simulation Results"]
        if self.raw:
            lines.append(f"Analysis: {self.raw.plotname}")
            lines.append(f"Variables ({self.raw.num_variables}): {', '.join(v['name'] for v in self.raw.variables)}")
            lines.append(f"Data Points: {self.raw.num_points}")
            if self.raw.time is not None:
                lines.append(f"Time Range: {self.raw.time[0]:.6e} to {self.raw.time[-1]:.6e}")
            lines.append("")

        if self.measurements:
            lines.append("== Measurements ==")
            for name, val in self.measurements.items():
                lines.append(f"  {name} = {val:.6e}")
            lines.append("")

        lines.append("== Simulation Info ==")
        lines.append(f"  Solver: {self.solver}")
        lines.append(f"  Method: {self.method}")
        lines.append(f"  Temp: {self.temp}C")
        lines.append(f"  Elapsed: {self.elapsed_time:.3f}s")
        lines.append("")

        if self.errors:
            lines.append("== Errors ==")
            for e in self.errors:
                lines.append(f"  {e}")
            lines.append("")

        if self.warnings:
            lines.append("== Warnings ==")
            for w in self.warnings:
                lines.append(f"  {w}")
            lines.append("")

        if self.raw:
            lines.append("== Signal Summary ==")
            for var in self.raw.variables:
                name = var["name"]
                if name == "time":
                    continue
                data = self.raw.values.get(name)
                if data is not None and len(data) > 0:
                    try:
                        # Use NaN-safe functions to prevent crashes on simulation blowup
                        peak = float(np.nanmax(np.abs(data)))
                        mean = float(np.nanmean(data))
                        # For RMS, we handle NaN/Inf by checking if the result is valid
                        ms = np.nanmean(data ** 2)
                        rms = float(np.sqrt(ms)) if ms >= 0 else 0.0
                        
                        # Format with fallback for NaN/Inf values
                        p_str = f"{peak:.4e}" if np.isfinite(peak) else "NaN"
                        m_str = f"{mean:.4e}" if np.isfinite(mean) else "NaN"
                        r_str = f"{rms:.4e}" if np.isfinite(rms) else "NaN"
                        
                        lines.append(f"  {name}: peak={p_str}, mean={m_str}, rms={r_str}")
                    except Exception:
                        lines.append(f"  {name}: (calculation error)")

        return "\n".join(lines)

    def to_dict(self, include_data: bool = True) -> Dict[str, Any]:
        import math
        def _json_safe(v):
            if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
                return None
            return v

        d: Dict[str, Any] = {
            "successful": self.successful,
            "returncode": self.returncode,
            "plotname": self.raw.plotname if self.raw else "",
            "num_variables": self.raw.num_variables if self.raw else 0,
            "num_points": self.raw.num_points if self.raw else 0,
            "measurements": {k: _json_safe(v) for k, v in self.measurements.items()},
            "solver": self.solver,
            "method": self.method,
            "tnom": self.tnom,
            "temp": self.temp,
            "elapsed_time": self.elapsed_time,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }
        if self.raw:
            d["variables"] = [{"name": v["name"], "unit": v.get("unit", "")} for v in self.raw.variables]
            if include_data:
                # Sanitize entire data arrays for JSON
                d["data"] = {k: [(_json_safe(float(x))) for x in v] for k, v in self.raw.values.items()}
        return d

    def measurement(self, name: str, func: str, expression: str) -> "SimulationResult":
        if self.raw is None:
            raise ValueError("No raw data loaded")
        data = self.raw.values.get(expression)
        if data is None:
            raise ValueError(f"Variable '{expression}' not found in simulation data")

        if func.lower() == "max":
            self.measurements[name] = float(np.max(data))
        elif func.lower() == "min":
            self.measurements[name] = float(np.min(data))
        elif func.lower() == "rms":
            self.measurements[name] = float(np.sqrt(np.mean(data ** 2)))
        elif func.lower() == "peak_to_peak":
            self.measurements[name] = float(np.max(data) - np.min(data))
        elif func.lower() == "avg" or func.lower() == "average":
            self.measurements[name] = float(np.mean(data))
        elif func.lower() == "find" and "at" in expression.lower():
            pass
        else:
            try:
                self.measurements[name] = float(func)
            except ValueError:
                raise ValueError(f"Unknown measurement function: {func}")

        return self

    def fft(self, var_name: str, window: str = "hanning") -> Dict[str, np.ndarray]:
        if self.raw is None:
            raise ValueError("No raw data loaded")
        data = self.raw.values.get(var_name)
        if data is None:
            raise ValueError(f"Variable '{var_name}' not found")
        time = self.raw.time
        if time is None:
            raise ValueError("No time data available")

        n = len(data)
        dt = float(np.mean(np.diff(time)))
        fs = 1.0 / dt

        if window == "hanning":
            w = np.hanning(n)
        elif window == "hamming":
            w = np.hamming(n)
        elif window == "blackman":
            w = np.blackman(n)
        elif window == "bartlett":
            w = np.bartlett(n)
        else:
            w = np.ones(n)

        y = data * w
        Y = np.fft.rfft(y)
        freqs = np.fft.rfftfreq(n, d=dt)
        mag = np.abs(Y) * 2.0 / n
        phase = np.angle(Y, deg=True)

        return {
            "freq": freqs,
            "mag": mag,
            "phase": phase,
            "fs": fs,
            "dt": dt,
            "n": n,
        }

    def cleanup(self):
        paths = []
        if self.raw:
            paths.append(self.raw.filepath)
            op_raw = self.raw.filepath.with_suffix(".op.raw")
            if op_raw.exists():
                paths.append(op_raw)
        if self.log_path and self.log_path.exists():
            paths.append(self.log_path)
        if self.netlist_path and self.netlist_path.exists():
            paths.append(self.netlist_path)
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    def __repr__(self) -> str:
        return f"SimulationResult(plot={self.raw.plotname if self.raw else 'N/A'}, meas={len(self.measurements)}, pts={self.raw.num_points if self.raw else 0})"
