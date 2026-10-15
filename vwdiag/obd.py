"""OBD-II (SAE J1979) services over ISO-TP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .isotp import IsoTp
from .uds import UdsClient
from .vag import sae_code


@dataclass(frozen=True)
class Pid:
    pid: int
    key: str
    name: str
    unit: str
    length: int
    decode: Callable[[bytes], float]


def _u16(b: bytes) -> int:
    return (b[0] << 8) | b[1]


PIDS: Dict[int, Pid] = {
    p.pid: p
    for p in [
        Pid(0x04, "load", "Calculated engine load", "%", 1, lambda b: b[0] * 100 / 255),
        Pid(0x05, "coolant", "Coolant temperature", "°C", 1, lambda b: b[0] - 40),
        Pid(0x06, "stft1", "Short term fuel trim B1", "%", 1, lambda b: b[0] * 100 / 128 - 100),
        Pid(0x07, "ltft1", "Long term fuel trim B1", "%", 1, lambda b: b[0] * 100 / 128 - 100),
        Pid(0x0A, "fuelpress", "Fuel pressure (gauge)", "kPa", 1, lambda b: b[0] * 3),
        Pid(0x0B, "map", "Intake manifold pressure", "kPa", 1, lambda b: b[0]),
        Pid(0x0C, "rpm", "Engine speed", "rpm", 2, lambda b: _u16(b) / 4),
        Pid(0x0D, "speed", "Vehicle speed", "km/h", 1, lambda b: b[0]),
        Pid(0x0E, "timing", "Timing advance", "°", 1, lambda b: b[0] / 2 - 64),
        Pid(0x0F, "iat", "Intake air temperature", "°C", 1, lambda b: b[0] - 40),
        Pid(0x10, "maf", "Mass air flow", "g/s", 2, lambda b: _u16(b) / 100),
        Pid(0x11, "throttle", "Throttle position", "%", 1, lambda b: b[0] * 100 / 255),
        Pid(0x1F, "runtime", "Time since engine start", "s", 2, lambda b: _u16(b)),
        Pid(0x21, "dist_mil", "Distance with MIL on", "km", 2, lambda b: _u16(b)),
        Pid(0x2F, "fuel", "Fuel level", "%", 1, lambda b: b[0] * 100 / 255),
        Pid(0x33, "baro", "Barometric pressure", "kPa", 1, lambda b: b[0]),
        Pid(0x42, "voltage", "Control module voltage", "V", 2, lambda b: _u16(b) / 1000),
        Pid(0x46, "ambient", "Ambient air temperature", "°C", 1, lambda b: b[0] - 40),
        Pid(0x5C, "oil", "Engine oil temperature", "°C", 1, lambda b: b[0] - 40),
    ]
}
PIDS_BY_KEY = {p.key: p for p in PIDS.values()}


def resolve_pid(spec: str) -> Pid:
    s = spec.strip().lower()
    if s in PIDS_BY_KEY:
        return PIDS_BY_KEY[s]
    try:
        n = int(s, 16)
    except ValueError:
        raise KeyError(f"unknown PID {spec!r}") from None
    if n in PIDS:
        return PIDS[n]
    raise KeyError(f"PID {spec!r} has no decoder")


def decode_dtcs(data: bytes) -> List[str]:
    return [
        sae_code(data[i], data[i + 1])
        for i in range(0, len(data) - 1, 2)
        if data[i] or data[i + 1]
    ]


class ObdClient:
    def __init__(self, tp: IsoTp):
        self.uds = UdsClient(tp)  # same request/response mechanics

    def supported_pids(self) -> List[int]:
        found: List[int] = []
        base = 0x00
        while True:
            resp = self.uds.request(bytes([0x01, base]))
            mask = int.from_bytes(resp[2:6], "big")
            for i in range(32):
                if mask & (1 << (31 - i)):
                    found.append(base + i + 1)
            if not mask & 1:
                return found
            base += 0x20

    def read_pid(self, pid: Pid) -> float:
        resp = self.uds.request(bytes([0x01, pid.pid]))
        return pid.decode(resp[2 : 2 + pid.length])

    def stored_dtcs(self) -> List[str]:
        resp = self.uds.request(bytes([0x03]))
        return decode_dtcs(resp[2:])  # resp[1] is the count byte on CAN

    def pending_dtcs(self) -> List[str]:
        resp = self.uds.request(bytes([0x07]))
        return decode_dtcs(resp[2:])

    def clear_dtcs(self) -> None:
        self.uds.request(bytes([0x04]))

    def vin(self) -> Optional[str]:
        resp = self.uds.request(bytes([0x09, 0x02]))
        return resp[3:].rstrip(b"\0").decode("ascii", "replace")
