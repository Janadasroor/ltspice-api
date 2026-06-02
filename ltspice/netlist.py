from pathlib import Path
from typing import List, Optional, Union, Dict, Any


class Component:
    def __init__(self, name: str, nodes: List[str], value: str, **params):
        self.name = name
        self.nodes = nodes
        self.value = value
        self.params = params

    def to_spice(self) -> str:
        node_str = " ".join(self.nodes)
        extras = ""
        if self.params:
            extras = " " + " ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name} {node_str} {self.value}{extras}"


class Resistor(Component):
    def __init__(self, name: str, n_plus: str, n_minus: str, resistance: Union[float, str], **kwargs):
        super().__init__(name, [n_plus, n_minus], str(resistance), **kwargs)


class Capacitor(Component):
    def __init__(self, name: str, n_plus: str, n_minus: str, capacitance: Union[float, str], **kwargs):
        super().__init__(name, [n_plus, n_minus], str(capacitance), **kwargs)


class Inductor(Component):
    def __init__(self, name: str, n_plus: str, n_minus: str, inductance: Union[float, str], **kwargs):
        super().__init__(name, [n_plus, n_minus], str(inductance), **kwargs)


class Diode(Component):
    def __init__(self, name: str, anode: str, cathode: str, model: str, **kwargs):
        super().__init__(name, [anode, cathode], model, **kwargs)


class Mosfet(Component):
    def __init__(self, name: str, drain: str, gate: str, source: str, bulk: Optional[str] = None,
                 model: str = "NMOS", **kwargs):
        nodes = [drain, gate, source, bulk if bulk else source]
        super().__init__(name, nodes, model, **kwargs)


class Jfet(Component):
    def __init__(self, name: str, drain: str, gate: str, source: str, model: str, **kwargs):
        super().__init__(name, [drain, gate, source], model, **kwargs)


class VoltageSource(Component):
    def __init__(self, name: str, n_plus: str, n_minus: str, value: str, **kwargs):
        super().__init__(name, [n_plus, n_minus], value, **kwargs)


class CurrentSource(Component):
    def __init__(self, name: str, n_plus: str, n_minus: str, value: str, **kwargs):
        super().__init__(name, [n_plus, n_minus], value, **kwargs)


class Bjt(Component):
    def __init__(self, name: str, collector: str, base: str, emitter: str,
                 model: str, **kwargs):
        super().__init__(name, [collector, base, emitter], model, **kwargs)


class SubcircuitCall(Component):
    def __init__(self, name: str, nodes: List[str], subckt_name: str, **kwargs):
        super().__init__(name, nodes, subckt_name, **kwargs)


class Vcvs(Component):
    """Voltage-Controlled Voltage Source (E element)"""
    def __init__(self, name: str, n_plus: str, n_minus: str,
                 nc_plus: str, nc_minus: str, gain: Union[float, str], **kwargs):
        super().__init__(name, [n_plus, n_minus, nc_plus, nc_minus], str(gain), **kwargs)


class Vccs(Component):
    """Voltage-Controlled Current Source (G element)"""
    def __init__(self, name: str, n_plus: str, n_minus: str,
                 nc_plus: str, nc_minus: str, transconductance: Union[float, str], **kwargs):
        super().__init__(name, [n_plus, n_minus, nc_plus, nc_minus], str(transconductance), **kwargs)


class Cccs(Component):
    """Current-Controlled Current Source (F element)"""
    def __init__(self, name: str, n_plus: str, n_minus: str,
                 v_source_name: str, gain: Union[float, str], **kwargs):
        super().__init__(name, [n_plus, n_minus, v_source_name], str(gain), **kwargs)


class Ccvs(Component):
    """Current-Controlled Voltage Source (H element)"""
    def __init__(self, name: str, n_plus: str, n_minus: str,
                 v_source_name: str, transresistance: Union[float, str], **kwargs):
        super().__init__(name, [n_plus, n_minus, v_source_name], str(transresistance), **kwargs)


class BehavioralVoltage(Component):
    """Behavioral voltage source (BV)"""
    def __init__(self, name: str, n_plus: str, n_minus: str, expression: str, **kwargs):
        super().__init__(name, [n_plus, n_minus], expression, **kwargs)


class BehavioralCurrent(Component):
    """Behavioral current source (BI)"""
    def __init__(self, name: str, n_plus: str, n_minus: str, expression: str, **kwargs):
        super().__init__(name, [n_plus, n_minus], expression, **kwargs)


class MutualInductance(Component):
    def __init__(self, name: str, l1: str, l2: str, coupling: Union[float, str], **kwargs):
        super().__init__(name, [l1, l2], str(coupling), **kwargs)


class TransmissionLine(Component):
    def __init__(self, name: str, n1: str, n2: str, n3: str, n4: str,
                 impedance: Union[float, str], delay: Union[float, str], **kwargs):
        super().__init__(name, [n1, n2, n3, n4], f"Z0={impedance} TD={delay}", **kwargs)


class Switch(Component):
    def __init__(self, name: str, n1: str, n2: str, nc1: str, nc2: str,
                 model: str = "SW", **kwargs):
        super().__init__(name, [n1, n2, nc1, nc2], model, **kwargs)


class Netlist:
    def __init__(self, title: str = "LTspice Simulation"):
        self.title = title
        self.components: List[Component] = []
        self.controls: List[str] = []
        self.models: List[str] = []
        self.library: List[str] = []
        self.comments: List[str] = []
        self.params: Dict[str, Any] = {}
        self.options: Dict[str, Any] = {}
        self.meas: List[str] = []
        self.subckts: Dict[str, "Netlist"] = {}
        self._current_subckt: Optional[str] = None

    def add(self, component: Component) -> "Netlist":
        self.components.append(component)
        return self

    def param(self, name: str, value: Any) -> "Netlist":
        self.params[name] = value
        return self

    def model(self, text: str) -> "Netlist":
        self.models.append(text)
        return self

    def include(self, path: str) -> "Netlist":
        self.library.append(path)
        return self

    def option(self, name: str, value: Optional[Any] = None) -> "Netlist":
        self.options[name] = value
        return self

    def meas(self, name: str, analysis: str, meas_type: str,
             expression: str, **kwargs) -> "Netlist":
        parts = [analysis, meas_type, expression]
        for k, v in kwargs.items():
            parts.append(f"{k}={v}")
        self.meas.append(f".meas {name} {' '.join(parts)}")
        return self

    def four(self, freq: Union[float, str], outputs: List[str],
             n_harmonics: int = 9) -> "Netlist":
        self.controls.append(f".four {freq} {' '.join(outputs)}")
        return self

    def step(self, what: str, start: Union[float, str], stop: Union[float, str],
             step: Union[float, str]) -> "Netlist":
        self.controls.append(f".step {what} {start} {stop} {step}")
        return self

    def step_param(self, param: str, values: List[Any]) -> "Netlist":
        self.controls.append(f".step param {param} {' '.join(str(v) for v in values)}")
        return self

    def step_lin(self, param: str, start: Union[float, str], stop: Union[float, str],
                 steps: int) -> "Netlist":
        self.controls.append(f".step param {param} LIN {start} {stop} {steps}")
        return self

    def subcircuit(self, name: str) -> "Netlist":
        self._current_subckt = name
        self.subckts[name] = Netlist(title=f"Subcircuit: {name}")
        return self.subckts[name]

    def ends(self) -> "Netlist":
        self._current_subckt = None
        return self

    def control(self, text: str) -> "Netlist":
        self.controls.append(text)
        return self

    def comment(self, text: str) -> "Netlist":
        self.comments.append(text)
        return self

    def __str__(self) -> str:
        lines = [f"* {self.title}"]

        for c in self.comments:
            lines.append(f"* {c}")

        for k, v in self.params.items():
            lines.append(f".param {k}={v}")

        for opt_name, opt_val in self.options.items():
            if opt_val is None:
                lines.append(f".options {opt_name}")
            else:
                lines.append(f".options {opt_name}={opt_val}")

        for m in self.models:
            if not m.startswith(".") and not m.startswith("model "):
                lines.append(f".model {m}")
            else:
                lines.append(m)

        for lib in self.library:
            if not lib.startswith(".include"):
                lines.append(f".include {lib}")
            else:
                lines.append(lib)

        # Subcircuits
        for name, sub in self.subckts.items():
            lines.append(f".subckt {name}")
            for comp in sub.components:
                lines.append(comp.to_spice())
            lines.append(".ends")

        for comp in self.components:
            lines.append(comp.to_spice())

        for m in self.meas:
            lines.append(m)

        for ctrl in self.controls:
            if not ctrl.startswith("."):
                lines.append(f".{ctrl}")
            else:
                lines.append(ctrl)

        lines.append(".backanno")
        lines.append(".end")
        return "\n".join(lines) + "\n"

    def write(self, path: str) -> "Netlist":
        with open(path, "w") as f:
            f.write(str(self))
        return self

    def run(
        self,
        work_dir: Optional[Union[str, Path]] = None,
        filename: Optional[str] = None,
        timeout: Optional[int] = None,
        wait: bool = True,
        quiet: bool = False,
        **kwargs,
    ):
        from .simulator import run_simulation

        if work_dir:
            work_dir = Path(work_dir)
            work_dir.mkdir(parents=True, exist_ok=True)
        else:
            work_dir = Path.cwd()

        fname = filename or self.title.replace(" ", "_").lower()
        net_path = work_dir / f"{fname}.net"
        self.write(str(net_path))

        return run_simulation(net_path, timeout=timeout, wait=wait, quiet=quiet, **kwargs)

    def run_cir(self, **kwargs):
        return self.run(**kwargs)

    @staticmethod
    def from_file(path: str) -> "Netlist":
        n = Netlist()
        current_sub = n
        with open(path) as f:
            for line in f:
                raw = line.rstrip("\n")
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("*"):
                    current_sub.comments.append(line[1:].strip())
                    continue
                if line.startswith(".param"):
                    rest = line[7:].strip()
                    for pair in rest.split():
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            n.params[k.strip()] = v.strip()
                    continue
                if line.startswith(".model "):
                    current_sub.models.append(line[7:].strip())
                    continue
                if line.startswith(".include "):
                    current_sub.library.append(line[9:].strip())
                    continue
                if line.startswith(".options "):
                    rest = line[9:].strip()
                    for pair in rest.split():
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            n.options[k.strip()] = v.strip()
                        else:
                            n.options[pair] = None
                    continue
                if line.startswith(".meas"):
                    n.meas.append(line)
                    continue
                if line.startswith(".subckt "):
                    name = line[8:].strip().split()[0]
                    current_sub = Netlist(title=f"Subcircuit: {name}")
                    n.subckts[name] = current_sub
                    continue
                if line.startswith(".ends"):
                    current_sub = n
                    continue
                if line.startswith(".end") or line.startswith(".backanno"):
                    continue
                if line.startswith(".step"):
                    n.controls.append(line)
                    continue
                if line.startswith(".four"):
                    n.controls.append(line)
                    continue
                if line.startswith("."):
                    n.controls.append(line[1:] if not line[1:].startswith(".") else line)
                    continue

                parts = line.split()
                if len(parts) >= 3:
                    name = parts[0]
                    nodes = parts[1:-1]
                    value = parts[-1]
                    current_sub.components.append(Component(name, nodes, value))
        return n


class Circuit(Netlist):
    def resistor(self, name: str, n1: str, n2: str, value: Union[float, str], **kwargs) -> "Circuit":
        self.components.append(Resistor(name, n1, n2, value, **kwargs))
        return self

    def capacitor(self, name: str, n1: str, n2: str, value: Union[float, str], **kwargs) -> "Circuit":
        self.components.append(Capacitor(name, n1, n2, value, **kwargs))
        return self

    def inductor(self, name: str, n1: str, n2: str, value: Union[float, str], **kwargs) -> "Circuit":
        self.components.append(Inductor(name, n1, n2, value, **kwargs))
        return self

    def diode(self, name: str, a: str, c: str, model: str, **kwargs) -> "Circuit":
        self.components.append(Diode(name, a, c, model, **kwargs))
        return self

    def nmos(self, name: str, d: str, g: str, s: str, b: Optional[str] = None, model: str = "NMOS", **kwargs) -> "Circuit":
        self.components.append(Mosfet(name, d, g, s, b, model, **kwargs))
        return self

    def pmos(self, name: str, d: str, g: str, s: str, b: Optional[str] = None, model: str = "PMOS", **kwargs) -> "Circuit":
        self.components.append(Mosfet(name, d, g, s, b, model, **kwargs))
        return self

    def njfet(self, name: str, d: str, g: str, s: str, model: str = "NJF", **kwargs) -> "Circuit":
        self.components.append(Jfet(name, d, g, s, model, **kwargs))
        return self

    def pjfet(self, name: str, d: str, g: str, s: str, model: str = "PJF", **kwargs) -> "Circuit":
        self.components.append(Jfet(name, d, g, s, model, **kwargs))
        return self

    def npn(self, name: str, c: str, b: str, e: str, model: str = "NPN", **kwargs) -> "Circuit":
        self.components.append(Bjt(name, c, b, e, model, **kwargs))
        return self

    def pnp(self, name: str, c: str, b: str, e: str, model: str = "PNP", **kwargs) -> "Circuit":
        self.components.append(Bjt(name, c, b, e, model, **kwargs))
        return self

    def v(self, name: str, n1: str, n2: str, value: str, **kwargs) -> "Circuit":
        self.components.append(VoltageSource(name, n1, n2, value, **kwargs))
        return self

    def i(self, name: str, n1: str, n2: str, value: str, **kwargs) -> "Circuit":
        self.components.append(CurrentSource(name, n1, n2, value, **kwargs))
        return self

    def e(self, name: str, n_plus: str, n_minus: str,
          nc_plus: str, nc_minus: str, gain: Union[float, str], **kwargs) -> "Circuit":
        self.components.append(Vcvs(name, n_plus, n_minus, nc_plus, nc_minus, gain, **kwargs))
        return self

    def g(self, name: str, n_plus: str, n_minus: str,
          nc_plus: str, nc_minus: str, gm: Union[float, str], **kwargs) -> "Circuit":
        self.components.append(Vccs(name, n_plus, n_minus, nc_plus, nc_minus, gm, **kwargs))
        return self

    def f(self, name: str, n_plus: str, n_minus: str,
          v_sense: str, gain: Union[float, str], **kwargs) -> "Circuit":
        self.components.append(Cccs(name, n_plus, n_minus, v_sense, gain, **kwargs))
        return self

    def h(self, name: str, n_plus: str, n_minus: str,
          v_sense: str, gain: Union[float, str], **kwargs) -> "Circuit":
        self.components.append(Ccvs(name, n_plus, n_minus, v_sense, gain, **kwargs))
        return self

    def bv(self, name: str, n_plus: str, n_minus: str, expr: str, **kwargs) -> "Circuit":
        self.components.append(BehavioralVoltage(name, n_plus, n_minus, expr, **kwargs))
        return self

    def bi(self, name: str, n_plus: str, n_minus: str, expr: str, **kwargs) -> "Circuit":
        self.components.append(BehavioralCurrent(name, n_plus, n_minus, expr, **kwargs))
        return self

    def k(self, name: str, l1: str, l2: str, coupling: Union[float, str], **kwargs) -> "Circuit":
        self.components.append(MutualInductance(name, l1, l2, coupling, **kwargs))
        return self

    def tline(self, name: str, n1: str, n2: str, n3: str, n4: str,
              z0: Union[float, str], td: Union[float, str], **kwargs) -> "Circuit":
        self.components.append(TransmissionLine(name, n1, n2, n3, n4, z0, td, **kwargs))
        return self

    def sw(self, name: str, n1: str, n2: str, nc1: str, nc2: str,
           model: str = "SW", **kwargs) -> "Circuit":
        self.components.append(Switch(name, n1, n2, nc1, nc2, model, **kwargs))
        return self

    def sub(self, name: str, nodes: List[str], subckt: str, **kwargs) -> "Circuit":
        self.components.append(SubcircuitCall(name, nodes, subckt, **kwargs))
        return self

    def run(
        self,
        work_dir: Optional[Union[str, Path]] = None,
        filename: Optional[str] = None,
        timeout: Optional[int] = None,
        wait: bool = True,
        quiet: bool = False,
        tran: Optional[Union[str, bool]] = None,
        ac: Optional[Union[str, bool]] = None,
        dc: Optional[Union[str, bool]] = None,
        op: bool = False,
        **kwargs,
    ):
        if not self.controls and not any([tran, ac, dc, op]):
            analysis_type, analysis_val = self._auto_detect_analysis()
            if analysis_type == self._ANALYSIS_OP:
                op = True
            elif analysis_type == self._ANALYSIS_AC:
                ac = analysis_val
            elif analysis_type == "tran":
                if isinstance(analysis_val, str):
                    tran = analysis_val
                else:
                    tran = True

        if isinstance(tran, str):
            self.tran(tran)
        elif tran is True:
            self.tran(self._auto_tran_params())

        if isinstance(ac, str):
            self.control(f"ac {ac}")
        elif ac is True:
            self.control("ac dec 100 1 1meg")

        if isinstance(dc, str):
            self.control(f"dc {dc}")
        elif dc is True:
            self.control("dc V1 0 5 0.1")

        if op:
            self.op()

        return super().run(work_dir=work_dir, filename=filename,
                           timeout=timeout, wait=wait, quiet=quiet, **kwargs)

    _ANALYSIS_OP = "op"
    _ANALYSIS_AC = "ac"

    def _auto_detect_analysis(self):
        has_ac = False
        has_time_varying = False
        max_freq = 0.0
        for c in self.components:
            val = getattr(c, "value", "")
            val_upper = val.upper() if isinstance(val, str) else ""
            if "SINE" in val_upper or "PULSE" in val_upper or "EXP" in val_upper or "SFFM" in val_upper:
                has_time_varying = True
                if "SINE" in val_upper:
                    parts = val_upper.replace("(", " ").replace(")", " ").split()
                    for i, p in enumerate(parts):
                        if p == "SINE" and i + 3 < len(parts):
                            try:
                                freq = float(parts[i + 3])
                                max_freq = max(max_freq, freq)
                            except ValueError:
                                pass
            if "AC" in val_upper:
                has_ac = True

        if has_ac:
            return self._ANALYSIS_AC, "dec 100 1 1meg"
        if has_time_varying:
            if max_freq > 0:
                period = 1.0 / max_freq
                stop_time = max(period * 5, 1e-6)
                return "tran", f"0 {stop_time:.6e}"
            return "tran", True
        return self._ANALYSIS_OP, None

    def _auto_tran_params(self) -> str:
        return "1m"

    def tran(self, tstop: Union[float, str], tstep: Optional[Union[float, str]] = None,
             tstart: Optional[Union[float, str]] = None, **kwargs) -> "Circuit":
        parts = [str(tstop)]
        if tstep is not None:
            parts = [str(tstep), str(tstop)]
        if tstart is not None:
            parts.append(str(tstart))
        return self.control(f"tran {' '.join(parts)}")

    def ac(self, oct_dec: str, npts: int, fstart: Union[float, str],
           fstop: Union[float, str]) -> "Circuit":
        return self.control(f"ac {oct_dec} {npts} {fstart} {fstop}")

    def dc(self, source: str, start: Union[float, str], stop: Union[float, str],
           step: Union[float, str]) -> "Circuit":
        return self.control(f"dc {source} {start} {stop} {step}")

    def op(self) -> "Circuit":
        return self.control("op")

    def noise(self, output: str, source: str, oct_dec: str, npts: int,
              fstart: Union[float, str], fstop: Union[float, str]) -> "Circuit":
        return self.control(f"noise {output} {source} {oct_dec} {npts} {fstart} {fstop}")
