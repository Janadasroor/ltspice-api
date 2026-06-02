import subprocess
import os
from pathlib import Path
from typing import Optional, Union
from .raw_read import RawFile
from .result import SimulationResult


LTSPICE_EXE = Path(os.environ.get(
    "LTSPICE_EXE",
    r"C:\Users\js\AppData\Local\Programs\ADI\LTspice\LTspice.exe"
))


def _find_ltspice() -> Path:
    exe = LTSPICE_EXE
    if exe.exists():
        return exe
    candidates = [
        Path(r"C:\Program Files\ADI\LTspice\LTspice.exe"),
        Path(r"C:\Program Files\LTC\LTspiceXVII\XVIIx64.exe"),
        Path(os.environ.get("ProgramFiles", "")) / "ADI" / "LTspice" / "LTspice.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "LTspice executable not found. Set LTSPICE_EXE env var or install LTspice."
    )


def run_simulation(
    netlist_path: Union[str, Path],
    output_path: Optional[Union[str, Path]] = None,
    timeout: Optional[int] = None,
    wait: bool = True,
    ltspice_exe: Optional[Union[str, Path]] = None,
    quiet: bool = False,
) -> Optional[SimulationResult]:
    netlist_path = Path(netlist_path).resolve()
    if not netlist_path.exists():
        raise FileNotFoundError(f"Netlist not found: {netlist_path}")

    exe = Path(ltspice_exe) if ltspice_exe else _find_ltspice()
    cmd = [str(exe), "-b", "-Run", str(netlist_path)]

    if not quiet:
        print(f"Starting: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    if not wait:
        return proc

    stdout, stderr = proc.communicate(timeout=timeout)
    ret = proc.returncode
    netlist_text = netlist_path.read_text() if netlist_path.exists() else ""

    stdout_str = stdout.decode(errors="replace") if stdout else ""
    stderr_str = stderr.decode(errors="replace") if stderr else ""

    # Find the raw file
    raw_candidates = [
        netlist_path.with_suffix(".raw"),
        netlist_path.parent / (netlist_path.stem + ".raw"),
    ]
    if output_path:
        raw_candidates.insert(0, Path(output_path))

    raw = None
    for rc in raw_candidates:
        if rc.exists():
            raw = RawFile(rc)
            break

    # Find the log file
    log_candidates = [
        netlist_path.with_suffix(".log"),
        netlist_path.parent / (netlist_path.stem + ".log"),
    ]
    log_path = None
    for lc in log_candidates:
        if lc.exists():
            log_path = lc
            break

    return SimulationResult(
        raw=raw,
        log_path=log_path,
        netlist_text=netlist_text,
        netlist_path=netlist_path,
        stdout_text=stdout_str,
        stderr_text=stderr_str,
        returncode=ret,
    )


def run_netlist(
    netlist_text: str,
    work_dir: Optional[Union[str, Path]] = None,
    filename: str = "sim",
    **kwargs,
) -> Optional[SimulationResult]:
    if work_dir:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path.cwd()

    net_path = work_dir / f"{filename}.net"
    net_path.write_text(netlist_text)

    return run_simulation(net_path, **kwargs)


class Simulation:
    def __init__(self, netlist_path: Optional[Union[str, Path]] = None):
        self.netlist_path = Path(netlist_path) if netlist_path else None
        self._result: Optional[SimulationResult] = None
        self._process: Optional[subprocess.Popen] = None

    def run(
        self,
        netlist_text: Optional[str] = None,
        output_path: Optional[Union[str, Path]] = None,
        timeout: Optional[int] = None,
        wait: bool = True,
        async_result: bool = False,
        quiet: bool = False,
    ):
        if netlist_text:
            net_path = self.netlist_path or (Path.cwd() / "sim.net")
            net_path.parent.mkdir(parents=True, exist_ok=True)
            net_path.write_text(netlist_text)
            self.netlist_path = net_path

        if not self.netlist_path or not self.netlist_path.exists():
            raise FileNotFoundError(f"No netlist: {self.netlist_path}")

        exe = _find_ltspice()
        cmd = [str(exe), "-b", "-Run", str(self.netlist_path)]

        if not quiet:
            print(f"Starting: {' '.join(cmd)}")

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        if not wait:
            return self._process

        if async_result:
            return self

        stdout, stderr = self._process.communicate(timeout=timeout)
        stdout_str = stdout.decode(errors="replace") if stdout else ""
        stderr_str = stderr.decode(errors="replace") if stderr else ""

        raw_path = self.netlist_path.with_suffix(".raw")
        if output_path:
            raw_path = Path(output_path)
        log_path = self.netlist_path.with_suffix(".log")

        raw = None
        if raw_path.exists():
            raw = RawFile(raw_path)

        netlist_text = self.netlist_path.read_text() if self.netlist_path.exists() else ""

        self._result = SimulationResult(
            raw=raw,
            log_path=log_path if log_path.exists() else None,
            netlist_text=netlist_text,
            netlist_path=self.netlist_path,
            stdout_text=stdout_str,
            stderr_text=stderr_str,
            returncode=self._process.returncode,
        )

        return self._result

    @property
    def result(self) -> Optional[SimulationResult]:
        return self._result

    def wait(self, timeout: Optional[int] = None) -> "Simulation":
        if self._process:
            self._process.communicate(timeout=timeout)
            raw_path = self.netlist_path.with_suffix(".raw")
            log_path = self.netlist_path.with_suffix(".log")
            raw = None
            if raw_path.exists():
                raw = RawFile(raw_path)
            netlist_text = self.netlist_path.read_text() if self.netlist_path.exists() else ""
            self._result = SimulationResult(
                raw=raw,
                log_path=log_path if log_path.exists() else None,
                netlist_text=netlist_text,
                netlist_path=self.netlist_path,
            )
        return self
