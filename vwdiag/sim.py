"""Simulated VW ECUs on a virtual CAN network, for demos and tests."""

from __future__ import annotations

import math
import threading
import time
from typing import Dict, List, Optional

from .can import VirtualNetwork
from .isotp import IsoTp
from .vag import FUNCTIONAL_ID, MODULES, Dtc, Module


def _live_pids(t: float) -> Dict[int, bytes]:
    rpm = int((850 + 120 * math.sin(t)) * 4)
    return {
        0x04: bytes([int(30 + 10 * math.sin(t / 3))]),
        0x05: bytes([90 + 40]),
        0x0B: bytes([35]),
        0x0C: rpm.to_bytes(2, "big"),
        0x0D: bytes([0]),
        0x0F: bytes([25 + 40]),
        0x11: bytes([int(255 * 0.15)]),
        0x1F: int(t).to_bytes(2, "big"),
        0x2F: bytes([140]),
        0x42: (14100).to_bytes(2, "big"),
    }


class SimEcu(threading.Thread):
    def __init__(self, net: VirtualNetwork, module: Module, dids: Dict[int, bytes],
                 dtcs: List[Dtc], obd: bool = False):
        super().__init__(daemon=True)
        self.module, self.dids, self.dtcs, self.obd = module, dids, list(dtcs), obd
        self.bus = net.bus()
        self.tp = IsoTp(self.bus, module.rx_id, module.tx_id,
                        also_accept=(FUNCTIONAL_ID,) if obd else ())
        self.vin = dids.get(0xF190, b"")
        self.t0 = time.monotonic()
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            req = self.tp.recv(0.05)
            if req:
                resp = self.handle(req)
                if resp:
                    self.tp.send(resp)
        self.bus.close()

    @staticmethod
    def _neg(sid: int, nrc: int) -> bytes:
        return bytes([0x7F, sid, nrc])

    def handle(self, req: bytes) -> Optional[bytes]:
        sid = req[0]
        if sid == 0x10 and len(req) == 2:
            return bytes([0x50, req[1], 0x00, 0x32, 0x01, 0xF4])
        if sid == 0x3E:
            return bytes([0x7E, 0x00])
        if sid == 0x11 and len(req) == 2:
            return bytes([0x51, req[1]])
        if sid == 0x22 and len(req) == 3:
            did = int.from_bytes(req[1:3], "big")
            if did in self.dids:
                return bytes([0x62]) + req[1:3] + self.dids[did]
            return self._neg(sid, 0x31)
        if sid == 0x19 and len(req) == 3 and req[1] == 0x02:
            body = b"".join(d.raw.to_bytes(3, "big") + bytes([d.status]) for d in self.dtcs
                            if d.status & req[2])
            return bytes([0x59, 0x02, 0xFF]) + body
        if sid == 0x14 and len(req) == 4:
            self.dtcs.clear()
            return bytes([0x54])
        if self.obd:
            return self._obd(req)
        return self._neg(sid, 0x11)

    def _obd(self, req: bytes) -> Optional[bytes]:
        sid = req[0]
        if sid == 0x01 and len(req) == 2:
            live = _live_pids(time.monotonic() - self.t0)
            pid = req[1]
            if pid % 0x20 == 0:
                mask = 0
                for p in live:
                    if pid < p <= pid + 0x20:
                        mask |= 1 << (32 - (p - pid))
                if any(p > pid + 0x20 for p in live):
                    mask |= 1
                return bytes([0x41, pid]) + mask.to_bytes(4, "big")
            if pid in live:
                return bytes([0x41, pid]) + live[pid]
            return None  # functional requests get no answer for unsupported PIDs
        if sid in (0x03, 0x07):
            sel = [d for d in self.dtcs if bool(d.status & 0x08) == (sid == 0x03)]
            body = b"".join((d.raw >> 8).to_bytes(2, "big") for d in sel)
            return bytes([sid + 0x40, len(sel)]) + body
        if sid == 0x04:
            self.dtcs.clear()
            return bytes([0x44])
        if sid == 0x09 and len(req) == 2 and req[1] == 0x02:
            return bytes([0x49, 0x02, 0x01]) + self.vin
        return None


class Simulator:
    """A handful of ECUs; `bus()` yields a tester-side bus."""

    VIN = b"WVWZZZ1KZAW000001"

    def __init__(self) -> None:
        self.net = VirtualNetwork()
        by_addr = {m.address: m for m in MODULES}

        def ident(part: bytes, sw: bytes) -> Dict[int, bytes]:
            return {0xF190: self.VIN, 0xF187: part, 0xF189: sw, 0xF191: b"H01",
                    0xF18C: b"SN" + part[-6:]}

        self.ecus = [
            SimEcu(self.net, by_addr[0x01], ident(b"06K906259BQ", b"0001"),
                   [Dtc(0x030000, 0x2F), Dtc(0x013400, 0x04)], obd=True),
            SimEcu(self.net, by_addr[0x03], ident(b"5Q0614517AB", b"0382"),
                   [Dtc(0xC12100, 0x2F)]),
            SimEcu(self.net, by_addr[0x17], ident(b"5G0920860E", b"0427"), []),
            SimEcu(self.net, by_addr[0x19], ident(b"5Q0907530L", b"0510"), []),
        ]
        for e in self.ecus:
            e.start()

    def bus(self):
        return self.net.bus()

    def close(self) -> None:
        for e in self.ecus:
            e.stop()
        for e in self.ecus:
            e.join(1)
