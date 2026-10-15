"""CAN bus backends: Linux SocketCAN and an in-process virtual bus."""

from __future__ import annotations

import queue
import socket
import struct
import threading
from dataclasses import dataclass
from typing import List, Optional

CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
_FMT = "=IB3x8s"
_SIZE = struct.calcsize(_FMT)


@dataclass
class Frame:
    arbitration_id: int
    data: bytes
    extended: bool = False

    def __str__(self) -> str:
        width = 8 if self.extended else 3
        return f"{self.arbitration_id:0{width}X}#{self.data.hex().upper()}"


class Bus:
    def send(self, frame: Frame) -> None:
        raise NotImplementedError

    def recv(self, timeout: Optional[float] = None) -> Optional[Frame]:
        """Return the next frame, or None on timeout (timeout=0 polls)."""
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class SocketCanBus(Bus):
    """Raw SocketCAN socket (can0, vcan0, slcan0, ...). Linux only."""

    def __init__(self, channel: str):
        if not hasattr(socket, "AF_CAN"):
            raise OSError("SocketCAN is only available on Linux")
        self.channel = channel
        self._sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self._sock.bind((channel,))

    def send(self, frame: Frame) -> None:
        can_id = frame.arbitration_id | (CAN_EFF_FLAG if frame.extended else 0)
        self._sock.send(struct.pack(_FMT, can_id, len(frame.data), frame.data.ljust(8, b"\0")))

    def recv(self, timeout: Optional[float] = None) -> Optional[Frame]:
        self._sock.settimeout(timeout)
        while True:
            try:
                raw = self._sock.recv(_SIZE)
            except (socket.timeout, BlockingIOError):
                return None
            if len(raw) < _SIZE:
                continue
            can_id, dlc, data = struct.unpack(_FMT, raw)
            if can_id & (CAN_ERR_FLAG | CAN_RTR_FLAG):
                continue
            ext = bool(can_id & CAN_EFF_FLAG)
            return Frame(can_id & (0x1FFFFFFF if ext else 0x7FF), data[:dlc], ext)

    def close(self) -> None:
        self._sock.close()


class VirtualNetwork:
    """In-process CAN network: every frame sent is delivered to all other buses."""

    def __init__(self) -> None:
        self._buses: List["VirtualBus"] = []
        self._lock = threading.Lock()

    def bus(self) -> "VirtualBus":
        b = VirtualBus(self)
        with self._lock:
            self._buses.append(b)
        return b

    def _deliver(self, sender: "VirtualBus", frame: Frame) -> None:
        with self._lock:
            targets = [b for b in self._buses if b is not sender]
        for b in targets:
            b._queue.put(frame)

    def _remove(self, bus: "VirtualBus") -> None:
        with self._lock:
            if bus in self._buses:
                self._buses.remove(bus)


class VirtualBus(Bus):
    def __init__(self, network: VirtualNetwork):
        self._net = network
        self._queue: "queue.Queue[Frame]" = queue.Queue()

    def send(self, frame: Frame) -> None:
        self._net._deliver(self, frame)

    def recv(self, timeout: Optional[float] = None) -> Optional[Frame]:
        try:
            if timeout is not None and timeout <= 0:
                return self._queue.get_nowait()
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._net._remove(self)
