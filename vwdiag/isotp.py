"""ISO 15765-2 (ISO-TP) transport over a raw CAN bus, pure Python."""

from __future__ import annotations

import time
from typing import Optional, Tuple

from .can import Bus, Frame


class IsoTpError(Exception):
    pass


class IsoTpTimeout(IsoTpError):
    pass


def _stmin_seconds(v: int) -> float:
    if v <= 0x7F:
        return v / 1000.0
    if 0xF1 <= v <= 0xF9:
        return (v - 0xF0) / 10000.0
    return 0.127


class IsoTp:
    """One ISO-TP channel: we transmit on `tx_id`, listen on `rx_id`.

    `also_accept` lists extra CAN ids whose frames are treated as incoming
    requests (used by simulated ECUs for functional address 0x7DF).
    """

    def __init__(
        self,
        bus: Bus,
        tx_id: int,
        rx_id: int,
        *,
        extended: bool = False,
        padding: Optional[int] = 0xAA,
        block_size: int = 0,
        stmin: int = 0,
        timeout: float = 1.0,
        also_accept: Tuple[int, ...] = (),
    ):
        self.bus = bus
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.extended = extended
        self.padding = padding
        self.block_size = block_size
        self.stmin = stmin
        self.timeout = timeout
        self._accept = {rx_id, *also_accept}
        self.last_source: int = rx_id

    # -- low level ---------------------------------------------------------
    def _tx(self, data: bytes) -> None:
        if self.padding is not None:
            data = data.ljust(8, bytes([self.padding]))
        self.bus.send(Frame(self.tx_id, data, self.extended))

    def _rx(self, timeout: float) -> Optional[Frame]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            f = self.bus.recv(max(remaining, 0))
            if f is None:
                return None
            if f.arbitration_id in self._accept and f.extended == self.extended and f.data:
                self.last_source = f.arbitration_id
                return f
            if remaining < -1.0:  # saturated bus: give up eventually
                return None

    def flush(self) -> None:
        while self.bus.recv(0) is not None:
            pass

    # -- sending -----------------------------------------------------------
    def send(self, payload: bytes) -> None:
        n = len(payload)
        if n == 0:
            raise IsoTpError("empty payload")
        if n <= 7:
            self._tx(bytes([n]) + payload)
            return
        if n > 4095:
            raise IsoTpError("payload too large for classic ISO-TP")
        self._tx(bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6])
        offset, sn = 6, 1
        bs, stmin = self._wait_fc()
        sent_in_block = 0
        while offset < n:
            self._tx(bytes([0x20 | sn]) + payload[offset : offset + 7])
            offset += 7
            sn = (sn + 1) & 0xF
            sent_in_block += 1
            if offset >= n:
                break
            if bs and sent_in_block >= bs:
                bs, stmin = self._wait_fc()
                sent_in_block = 0
            elif stmin:
                time.sleep(_stmin_seconds(stmin))

    def _wait_fc(self) -> Tuple[int, int]:
        waits = 0
        while True:
            f = self._rx(self.timeout)
            if f is None:
                raise IsoTpTimeout("no flow control frame")
            if f.data[0] >> 4 != 3 or len(f.data) < 3:
                continue
            fs = f.data[0] & 0xF
            if fs == 0:
                return f.data[1], f.data[2]
            if fs == 1:
                waits += 1
                if waits > 10:
                    raise IsoTpError("too many WAIT flow control frames")
                continue
            raise IsoTpError("receiver reported overflow")

    # -- receiving ---------------------------------------------------------
    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Receive one message; None on timeout."""
        f = self._rx(self.timeout if timeout is None else timeout)
        while f is not None:
            pci = f.data[0] >> 4
            if pci == 0:
                length = f.data[0] & 0xF
                if 1 <= length <= len(f.data) - 1:
                    return bytes(f.data[1 : 1 + length])
            elif pci == 1 and len(f.data) >= 3:
                total = ((f.data[0] & 0xF) << 8) | f.data[1]
                if total > 7:
                    return self._recv_multi(total, bytes(f.data[2:8]))
            # not a start frame (stray CF/FC etc.): skip it
            f = self._rx(0.0)
        return None

    def _recv_multi(self, total: int, first: bytes) -> bytes:
        buf = bytearray(first)
        self._tx(bytes([0x30, self.block_size, self.stmin]))
        sn, in_block = 1, 0
        while len(buf) < total:
            f = self._rx(1.0)
            if f is None:
                raise IsoTpTimeout("timeout waiting for consecutive frame")
            if f.data[0] >> 4 != 2:
                continue
            if f.data[0] & 0xF != sn:
                raise IsoTpError("consecutive frame sequence error")
            buf += f.data[1:8]
            sn = (sn + 1) & 0xF
            in_block += 1
            if self.block_size and in_block >= self.block_size and len(buf) < total:
                self._tx(bytes([0x30, self.block_size, self.stmin]))
                in_block = 0
        return bytes(buf[:total])
