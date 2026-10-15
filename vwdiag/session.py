"""Protocol-agnostic diagnostic session: UDS over ISO-TP or KWP2000 over TP 2.0."""

from __future__ import annotations

from typing import List, Optional, Tuple

from .can import Bus
from .isotp import IsoTp, IsoTpError
from .tp20 import Tp20Channel, Tp20Error
from .uds import KwpClient, UdsClient, UdsError, UdsNegativeResponse, UdsTimeout
from .vag import IDENT_DIDS, Module


def printable(b: bytes) -> str:
    if all(32 <= x < 127 or x in (0, 255) for x in b):
        return b.rstrip(b"\0\xff ").decode("ascii", "replace")
    return b.hex().upper()


def ascii_fields(b: bytes) -> str:
    """Show the printable runs of an ident record, e.g. 'part | version'."""
    runs, cur = [], bytearray()
    for x in b:
        if 32 <= x < 127:
            cur.append(x)
        elif cur:
            runs.append(cur.decode().strip())
            cur = bytearray()
    if cur:
        runs.append(cur.decode().strip())
    return " | ".join(r for r in runs if r) or b.hex().upper()


class UdsDiag:
    protocol = "UDS/ISO-TP"

    def __init__(self, tp: IsoTp, timeout: float):
        self.tp = tp
        self.client = UdsClient(tp, p2=timeout)

    def ident(self) -> List[Tuple[str, str]]:
        out = []
        for did, name in IDENT_DIDS.items():
            try:
                out.append((name, printable(self.client.read_did(did))))
            except UdsNegativeResponse:
                continue
        return out

    def dtcs(self):
        return self.client.read_dtcs()

    def clear(self) -> None:
        self.client.clear_dtcs()

    def reset(self, kind: int) -> None:
        self.client.ecu_reset(kind)

    def session(self, kind: str) -> None:
        self.client.session({"default": 1, "programming": 2, "extended": 3}[kind])

    def did(self, did: int) -> bytes:
        return self.client.read_did(did)

    def close(self) -> None:
        pass


class KwpDiag:
    protocol = "KWP2000/TP2.0"

    def __init__(self, chan: Tp20Channel, timeout: float):
        self.tp = chan
        self.client = KwpClient(chan, p2=timeout)
        try:
            self.client.start_session(0x89)
        except UdsNegativeResponse:
            pass  # some modules refuse; they still talk in the default session

    def ident(self) -> List[Tuple[str, str]]:
        out = []
        for opt, name in ((0x9B, "ECU identification"), (0x90, "VIN")):
            try:
                out.append((name, ascii_fields(self.client.read_ident(opt))))
            except UdsNegativeResponse:
                continue
        return out

    def dtcs(self):
        return self.client.read_dtcs()

    def clear(self) -> None:
        self.client.clear_dtcs()

    def reset(self, kind: int) -> None:
        self.client.ecu_reset(1)

    def session(self, kind: str) -> None:
        if kind == "programming":
            raise UdsError("programming session is not supported over KWP2000")
        self.client.start_session({"default": 0x81, "extended": 0x89}[kind])

    def did(self, did: int) -> bytes:
        raise UdsError("'did' is UDS only; use 'raw' for KWP2000 requests")

    def close(self) -> None:
        self.tp.close()


def connect(bus: Bus, module: Module, protocol: str = "auto", *, timeout: float = 1.0,
            padding: Optional[int] = 0xAA, tx: Optional[int] = None, rx: Optional[int] = None,
            probe_timeout: float = 0.3):
    """Open a session to a module.

    `auto` tries a TP 2.0 channel first (cars up to ~2012 answer it, and their
    engine ECUs also answer plain OBD on the UDS ids), then falls back to UDS.
    """
    if protocol in ("auto", "tp20"):
        try:
            chan = Tp20Channel.open(bus, module.address,
                                    timeout=probe_timeout if protocol == "auto" else timeout)
            chan.timeout = timeout
            return KwpDiag(chan, timeout)
        except Tp20Error as e:
            if protocol == "tp20":
                raise UdsTimeout(str(e)) from None
    tx = module.tx_id if tx is None else tx
    rx = module.rx_id if rx is None else rx
    tp = IsoTp(bus, tx, rx, padding=padding, timeout=timeout)
    if protocol == "uds":
        return UdsDiag(tp, timeout)
    probe = UdsClient(IsoTp(bus, tx, rx, padding=padding, timeout=probe_timeout), p2=probe_timeout)
    try:
        probe.tester_present()
    except UdsNegativeResponse:
        pass  # it answered, so UDS is spoken here
    except (UdsTimeout, IsoTpError):
        raise UdsTimeout("no response on TP 2.0 or UDS") from None
    return UdsDiag(tp, timeout)
