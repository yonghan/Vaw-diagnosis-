"""VW Transport Protocol 2.0 (TP 2.0) over CAN, as used by KWP2000-on-CAN cars.

Typical of VW Group cars before UDS took over (roughly 2003-2012). A channel is
opened per module on CAN id 0x200, after which both sides move to dedicated ids.

Frame layout (from public documentation of the protocol; not yet verified
against a real car by this project):
  setup request   0x200      dest C0 00 10 <rx_id lo hi> 01
  setup response  0x200+dest 00 D0 <ecu_tx lo hi> <ecu_rx lo hi> 01
  parameters      A0/A1 BS T1 T2 T3 T4        keep-alive  A3 (answered by A1)
  disconnect      A8 (answered by A8)
  data            <op><seq> ...   op 0/1 = wait for ACK (more/last), 2/3 = no ACK
  ACK             B<next seq>
The first data frame of a message carries a 2-byte big-endian length.
"""

from __future__ import annotations

import time
from typing import Optional

from .can import Bus, Frame

SETUP_ID = 0x200


class Tp20Error(Exception):
    pass


class Tp20Timeout(Tp20Error):
    pass


def _timing(b: int) -> float:
    """Decode a TP 2.0 timing byte: 2 bits unit (0.1ms,1ms,10ms,100ms), 6 bits value."""
    if b == 0xFF:
        return 0.0
    return (b & 0x3F) * (0.0001, 0.001, 0.01, 0.1)[b >> 6]


class Tp20Channel:
    """An established TP 2.0 channel. Same send/recv/flush shape as IsoTp."""

    def __init__(self, bus: Bus, tx_id: int, rx_id: int, *, block_size: int = 0x0F,
                 t1: int = 0x8A, t3: int = 0x32, timeout: float = 1.0):
        self.bus, self.tx_id, self.rx_id = bus, tx_id, rx_id
        self.block_size, self.t1, self.t3 = block_size, t1, t3
        self.timeout = timeout
        self.peer_bs = block_size
        self.peer_t3 = _timing(t3)
        self.closed = False
        self._seq = 0
        self._rbuf: Optional[bytearray] = None
        self._rlen = 0
        self._last = time.monotonic()

    # -- establishing --------------------------------------------------------
    @classmethod
    def open(cls, bus: Bus, dest: int, timeout: float = 1.0, tester_rx: int = 0x300) -> "Tp20Channel":
        while bus.recv(0) is not None:
            pass
        req = bytes([dest, 0xC0, 0x00, 0x10]) + tester_rx.to_bytes(2, "little") + b"\x01"
        bus.send(Frame(SETUP_ID, req))
        deadline = time.monotonic() + timeout
        while True:
            f = bus.recv(max(deadline - time.monotonic(), 0))
            if f is None:
                raise Tp20Timeout(f"module {dest:02X} did not answer TP 2.0 channel setup")
            if f.arbitration_id != SETUP_ID + dest or len(f.data) < 6:
                continue
            if f.data[1] == 0xD0:
                break
            if f.data[1] in (0xD6, 0xD7, 0xD8):
                raise Tp20Error(f"module {dest:02X} refused TP 2.0 channel (0x{f.data[1]:02X})")
        ecu_tx = int.from_bytes(f.data[2:4], "little")  # tester listens here
        ecu_rx = int.from_bytes(f.data[4:6], "little")  # tester transmits here
        ch = cls(bus, tx_id=ecu_rx, rx_id=ecu_tx, timeout=timeout)
        ch._negotiate()
        return ch

    def _params(self, op: int) -> bytes:
        return bytes([op, self.block_size, self.t1, 0xFF, self.t3, 0xFF])

    def _negotiate(self) -> None:
        self._tx(self._params(0xA0))
        f = self._rx_until(lambda d: d[0] == 0xA1 and len(d) >= 6, self.timeout)
        if f is None:
            raise Tp20Timeout("no channel parameter reply")
        self.peer_bs = f.data[1] or 0x0F
        self.peer_t3 = _timing(f.data[4])

    # -- low level -----------------------------------------------------------
    def _tx(self, data: bytes) -> None:
        self._last = time.monotonic()
        self.bus.send(Frame(self.tx_id, data))

    def _rx_until(self, pred, timeout: float) -> Optional[Frame]:
        deadline = time.monotonic() + timeout
        while True:
            f = self.bus.recv(max(deadline - time.monotonic(), 0))
            if f is None:
                return None
            if f.arbitration_id == self.rx_id and f.data and pred(f.data):
                return f
            if deadline - time.monotonic() < -1.0:
                return None

    def flush(self) -> None:
        while self.bus.recv(0) is not None:
            pass

    # -- channel maintenance ---------------------------------------------------
    def ping(self) -> None:
        self._tx(b"\xA3")
        if self._rx_until(lambda d: d[0] == 0xA1, self.timeout) is None:
            raise Tp20Timeout("keep-alive not answered")

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._tx(b"\xA8")
            self._rx_until(lambda d: d[0] == 0xA8, 0.2)
        finally:
            self.closed = True

    # -- sending ---------------------------------------------------------------
    def send(self, payload: bytes) -> None:
        if not payload:
            raise Tp20Error("empty payload")
        if time.monotonic() - self._last > 0.8:
            self.ping()
        data = len(payload).to_bytes(2, "big") + payload
        chunks = [data[i : i + 7] for i in range(0, len(data), 7)]
        in_block = 0
        for i, chunk in enumerate(chunks):
            in_block += 1
            if i == len(chunks) - 1:
                op = 1
            elif in_block >= self.peer_bs:
                op = 0
            else:
                op = 2
            self._tx(bytes([(op << 4) | self._seq]) + chunk)
            self._seq = (self._seq + 1) & 0xF
            if op in (0, 1):
                self._wait_ack()
                in_block = 0
            elif self.peer_t3:
                time.sleep(self.peer_t3)

    def _wait_ack(self) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            f = self._rx_until(lambda d: (d[0] >> 4) in (0x9, 0xB), max(deadline - time.monotonic(), 0))
            if f is None:
                raise Tp20Timeout("no ACK from module")
            if f.data[0] >> 4 == 0xB:
                return
            # 0x9x = receiver not ready; keep waiting

    # -- receiving -------------------------------------------------------------
    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Return one complete message, or None on timeout / channel closed."""
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while not self.closed:
            f = self._rx_until(lambda d: True, max(deadline - time.monotonic(), 0))
            if f is None:
                return None
            b0, hi = f.data[0], f.data[0] >> 4
            if hi <= 3:
                self._last = time.monotonic()
                if self._rbuf is None:
                    if len(f.data) < 3:
                        continue
                    self._rlen = int.from_bytes(f.data[1:3], "big")
                    self._rbuf = bytearray(f.data[3:])
                else:
                    self._rbuf += f.data[1:]
                if hi in (0, 1):
                    self._tx(bytes([0xB0 | (((b0 & 0xF) + 1) & 0xF)]))
                if hi in (1, 3):
                    msg, self._rbuf = bytes(self._rbuf[: self._rlen]), None
                    return msg
            elif b0 in (0xA0, 0xA3):
                self._tx(self._params(0xA1))
            elif b0 == 0xA8:
                self._tx(b"\xA8")
                self.closed = True
                return None
        return None
