import struct
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Union


class RawFile:
    def __init__(self, filepath: Union[str, Path]):
        self.filepath = Path(filepath)
        self.title = ""
        self.date = ""
        self.plotname = ""
        self.flags: str = ""
        self.num_variables = 0
        self.num_points = 0
        self.offset = 0.0
        self.command = ""
        self.variables: List[Dict] = []
        self.values: Dict[str, np.ndarray] = {}
        self._raw_binary = False
        self._read()

    def _decode_utf16(self, data: bytes) -> str:
        try:
            return data.decode("utf-16-le").rstrip("\x00").strip()
        except UnicodeDecodeError:
            return data.decode("latin-1").rstrip("\x00").strip()

    def _read(self):
        file_size = self.filepath.stat().st_size
        if file_size == 0:
            raise ValueError(f"Empty raw file: {self.filepath}")

        with open(self.filepath, "rb") as f:
            raw = f.read()

        # Read header lines until "Binary:" or "Values:" (UTF-16-LE encoded)
        binary_marker = b"B\x00i\x00n\x00a\x00r\x00y\x00:\x00\n\x00"
        values_marker = b"V\x00a\x00l\x00u\x00e\x00s\x00:\x00\n\x00"
        header_end = raw.find(binary_marker)
        if header_end != -1:
            self._raw_binary = True
        else:
            header_end = raw.find(values_marker)
            if header_end == -1:
                # Try ASCII/Latin-1 markers as fallback
                header_end = raw.find(b"Binary:\n")
                if header_end != -1:
                    self._raw_binary = True
                else:
                    header_end = raw.find(b"Values:\n")
                    if header_end == -1:
                        raise ValueError("Unrecognized .raw format: no Binary: or Values: marker")

        header = raw[:header_end].decode("utf-16-le", errors="replace")

        binary_marker_len = len(binary_marker)
        values_marker_len = len(values_marker)
        if self._raw_binary:
            header_end_pos = header_end + binary_marker_len
        else:
            header_end_pos = header_end + values_marker_len

        for line in header.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("Title:"):
                self.title = line[6:].strip()
            elif line.startswith("Date:"):
                self.date = line[5:].strip()
            elif line.startswith("Plotname:"):
                self.plotname = line[9:].strip()
            elif line.startswith("Flags:"):
                self.flags = line[6:].strip()
            elif line.startswith("No. Variables:"):
                self.num_variables = int(line[14:].strip())
            elif line.startswith("No. Points:"):
                self.num_points = int(line[11:].strip())
            elif line.startswith("Offset:"):
                self.offset = float(line[7:].strip())
            elif line.startswith("Command:"):
                self.command = line[8:].strip()
            elif line.startswith("Variables:"):
                continue
            elif line.startswith("\t") or (line and line[0].isdigit() and "\t" in line):
                parts = line.split("\t")
                if len(parts) >= 3:
                    idx = int(parts[0])
                    name = parts[1].strip()
                    self.variables.append({
                        "index": idx,
                        "name": name,
                        "unit": parts[2].strip() if len(parts) > 2 else "",
                    })

        data_bytes = raw[header_end_pos:]

        if self._raw_binary:
            self._read_binary(data_bytes)
        else:
            self._read_ascii(data_bytes)

    def _read_binary(self, data: bytes):
        n_vars = self.num_variables
        n_pts = self.num_points
        is_complex = "complex" in self.flags
        has_time = any(v["name"] == "time" for v in self.variables)

        if has_time:
            # Time is stored as double (8 bytes), other variables as float/complex
            if is_complex:
                var_dtype = np.dtype(np.complex64)
                per_point = 8 + (n_vars - 1) * 8
            else:
                var_dtype = np.dtype(np.float32)
                per_point = 8 + (n_vars - 1) * 4

            actual_pts = len(data) // per_point
            if actual_pts < n_pts:
                n_pts = actual_pts
                self.num_points = n_pts

            n_non_time = n_vars - 1
            non_time_vars = [v for v in self.variables if v["name"] != "time"]

            dt = np.dtype([
                ("time", np.float64),
                ("vars", var_dtype, n_non_time),
            ])
            arr = np.frombuffer(data, dtype=dt, count=n_pts)

            self.values["time"] = arr["time"].copy()
            var_data = arr["vars"]

            for i, var in enumerate(non_time_vars):
                self.values[var["name"]] = var_data[:, i].copy()
        else:
            # No time variable: all variables stored as float/complex
            if is_complex:
                var_dtype = np.dtype(np.complex64)
            else:
                var_dtype = np.dtype(np.float32)

            per_point = n_vars * var_dtype.itemsize
            actual_pts = len(data) // per_point
            if actual_pts < n_pts:
                n_pts = actual_pts
                self.num_points = n_pts

            arr = np.frombuffer(data, dtype=var_dtype, count=n_vars * n_pts)
            if n_pts > 0 and n_vars > 0:
                arr = arr.reshape((n_pts, n_vars), order="C")
                for i, var in enumerate(self.variables):
                    if i < arr.shape[1]:
                        self.values[var["name"]] = arr[:, i].copy()

    def _read_ascii(self, data: bytes):
        text = data.decode("latin-1", errors="replace")
        lines = text.strip().split("\n")
        n_vars = self.num_variables

        rows = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            row = []
            for p in parts:
                p = p.strip()
                try:
                    row.append(float(p))
                except ValueError:
                    row.append(np.nan)
            if len(row) == n_vars:
                rows.append(row)

        arr = np.array(rows, dtype=np.float64)
        self.num_points = arr.shape[0]

        for i, var in enumerate(self.variables):
            if i < arr.shape[1]:
                self.values[var["name"]] = arr[:, i]

    def get(self, name: str) -> np.ndarray:
        return self.values[name]

    @property
    def time(self) -> np.ndarray:
        for var in self.variables:
            if var["name"] == "time":
                return self.values["time"]
        return None

    def __repr__(self) -> str:
        return (
            f"RawFile({self.filepath.name!r})\n"
            f"  Title: {self.title}\n"
            f"  Plot:  {self.plotname}\n"
            f"  Vars:  {self.num_variables}, Points: {self.num_points}\n"
            f"  Flags: {self.flags}\n"
            f"  Variables: {[v['name'] for v in self.variables]}"
        )

    def keys(self) -> List[str]:
        return list(self.values.keys())

    def __getitem__(self, name: str) -> np.ndarray:
        return self.values[name]

    def to_dataframe(self):
        try:
            import pandas as pd
            return pd.DataFrame(self.values)
        except ImportError:
            raise ImportError("pandas is required for to_dataframe()")
