"""UDS (ISO 14229) client on top of ISO-TP."""

from __future__ import annotations

import time
from typing import List

from .isotp import IsoTp
from .vag import NRC_NAMES, Dtc


class UdsError(Exception):
    pass


class UdsTimeout(UdsError):
    pass


class UdsNegativeResponse(UdsError):
    def __init__(self, sid: int, nrc: int):
        self.sid, self.nrc = sid, nrc
        super().__init__(f"service 0x{sid:02X} rejected: 0x{nrc:02X} {NRC_NAMES.get(nrc, 'unknown')}")


class UdsClient:
    def __init__(self, tp: IsoTp, p2: float = 1.0, p2_star: float = 5.0):
        self.tp, self.p2, self.p2_star = tp, p2, p2_star

    def request(self, payload: bytes) -> bytes:
        """Send a request and return the positive response (starting at SID+0x40)."""
        sid = payload[0]
        self.tp.flush()
        self.tp.send(payload)
        deadline = time.monotonic() + self.p2
        while True:
            resp = self.tp.recv(max(deadline - time.monotonic(), 0))
            if resp is None:
                raise UdsTimeout(f"no response to service 0x{sid:02X}")
            if resp[0] == 0x7F and len(resp) >= 3 and resp[1] == sid:
                if resp[2] == 0x78:
                    deadline = time.monotonic() + self.p2_star
                    continue
                raise UdsNegativeResponse(sid, resp[2])
            if resp[0] == sid + 0x40:
                return resp

    # -- services ----------------------------------------------------------
    def session(self, kind: int = 0x01) -> bytes:
        return self.request(bytes([0x10, kind]))

    def tester_present(self) -> None:
        self.request(bytes([0x3E, 0x00]))

    def ecu_reset(self, kind: int = 0x01) -> None:
        self.request(bytes([0x11, kind]))

    def read_did(self, did: int) -> bytes:
        resp = self.request(bytes([0x22]) + did.to_bytes(2, "big"))
        return resp[3:]

    def read_dtcs(self, mask: int = 0xFF) -> List[Dtc]:
        resp = self.request(bytes([0x19, 0x02, mask]))
        body = resp[3:]
        return [
            Dtc(int.from_bytes(body[i : i + 3], "big"), body[i + 3])
            for i in range(0, len(body) - 3, 4)
        ]

    def clear_dtcs(self, group: int = 0xFFFFFF) -> None:
        self.request(bytes([0x14]) + group.to_bytes(3, "big"))
