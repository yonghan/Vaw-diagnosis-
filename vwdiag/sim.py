"""Simulated VW ECUs on a virtual CAN network, for demos and tests."""

from __future__ import annotations

import math
import threading
import time
from typing import Dict, List, Optional

from .can import VirtualNetwork
from .isotp import IsoTp
from .tp20 import SETUP_ID, Tp20Channel
from .vag import FUNCTIONAL_ID, MODULES, Dtc, KwpDtc, Module


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
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.is_set():
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


class SimTp20Ecu(threading.Thread):
    """A KWP2000-over-TP2.0 module (the way a ~2010 Audi talks)."""

    def __init__(self, net: VirtualNetwork, module: Module, ident: bytes, dtcs: List[KwpDtc]):
        super().__init__(daemon=True)
        self.module, self.ident_data, self.dtcs = module, ident, list(dtcs)
        self.bus = net.bus()
        self.chan: Optional[Tp20Channel] = None
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        addr = self.module.address
        while not self._halt.is_set():
            if self.chan is None:
                f = self.bus.recv(0.05)
                if (f and f.arbitration_id == SETUP_ID and len(f.data) >= 7
                        and f.data[0] == addr and f.data[1] == 0xC0):
                    ecu_tx = int.from_bytes(f.data[4:6], "little")  # tester receives here
                    ecu_rx = 0x740 + addr
                    self.chan = Tp20Channel(self.bus, tx_id=ecu_tx, rx_id=ecu_rx, t3=0x4A)
                    from .can import Frame

                    self.bus.send(Frame(SETUP_ID + addr, bytes([0x00, 0xD0])
                                        + ecu_tx.to_bytes(2, "little")
                                        + ecu_rx.to_bytes(2, "little") + b"\x01"))
                continue
            msg = self.chan.recv(0.05)
            if self.chan.closed:
                self.chan = None
            elif msg:
                resp = self.handle(msg)
                if resp:
                    self.chan.send(resp)
        self.bus.close()

    def handle(self, req: bytes) -> Optional[bytes]:
        sid = req[0]
        if sid == 0x10 and len(req) == 2:
            return bytes([0x50, req[1]])
        if sid == 0x3E:
            return bytes([0x7E])
        if sid == 0x1A and len(req) == 2:
            if req[1] == 0x9B:
                return bytes([0x5A, 0x9B]) + self.ident_data
            return bytes([0x7F, sid, 0x12])
        if sid == 0x18 and req[1:] == b"\x02\xff\x00":
            return (bytes([0x58, len(self.dtcs)])
                    + b"".join(d.code.to_bytes(2, "big") + bytes([d.status]) for d in self.dtcs))
        if sid == 0x14 and req[1:] == b"\xff\x00":
            self.dtcs.clear()
            return bytes([0x54, 0xFF, 0x00])
        return bytes([0x7F, sid, 0x11])


class Simulator:
    """A handful of ECUs; `bus()` yields a tester-side bus."""

    VIN = b"WVWZZZ1KZAW000001"

    def __init__(self, profile: str = "mqb") -> None:
        self.net = VirtualNetwork()
        by_addr = {m.address: m for m in MODULES}
        if profile == "q5":
            # ~2010 car: emissions OBD-II on 7E0, everything else KWP2000 over TP 2.0
            self.ecus = [
                SimEcu(self.net, by_addr[0x01], {0xF190: self.VIN},
                       [Dtc(0x030000, 0x2F), Dtc(0x013400, 0x04)], obd=True),
                SimTp20Ecu(self.net, by_addr[0x01], b"8R0907115A \x00 2.0l R4/4V TFSI  \x00 0012 \x00",
                           [KwpDtc(16684, 0x2F), KwpDtc(16518, 0x04), KwpDtc(17963, 0x2F)]),
                SimTp20Ecu(self.net, by_addr[0x03], b"8K0907379AB \x00 ESP  \x00 0201 \x00", []),
                SimTp20Ecu(self.net, by_addr[0x15], b"8K0959655C \x00 Airbag  \x00 0004 \x00",
                           [KwpDtc(1234, 0x20)]),
                SimTp20Ecu(self.net, by_addr[0x17], b"8R0920930A \x00 KOMBI \x00 0120 \x00", []),
            ]
            for e in self.ecus:
                e.start()
            return

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
