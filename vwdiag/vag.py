"""VW Group specifics: module addresses, DTC formatting, identification DIDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Module:
    address: int  # VAG "address word" (01 = Engine, 03 = Brakes, ...)
    name: str
    tx_id: int  # tester -> ECU
    rx_id: int  # ECU -> tester


# Typical UDS-on-CAN ids seen on MQB-era vehicles. Layouts vary by platform and
# model year; use --tx/--rx to override for a specific car.
MODULES: List[Module] = [
    Module(0x01, "Engine", 0x7E0, 0x7E8),
    Module(0x02, "Transmission", 0x7E1, 0x7E9),
    Module(0x03, "Brakes", 0x713, 0x77D),
    Module(0x08, "Auto HVAC", 0x708, 0x772),
    Module(0x09, "Central Electrics", 0x70E, 0x778),
    Module(0x15, "Airbags", 0x715, 0x77F),
    Module(0x16, "Steering Wheel", 0x70C, 0x776),
    Module(0x17, "Instruments", 0x714, 0x77E),
    Module(0x19, "CAN Gateway", 0x710, 0x77A),
    Module(0x44, "Steering Assist", 0x712, 0x77C),
]

FUNCTIONAL_ID = 0x7DF


def find_module(spec: str) -> Module:
    """Resolve '01', '0x03' or a name fragment such as 'engine'."""
    s = spec.strip().lower()
    try:
        addr = int(s, 16)
        for m in MODULES:
            if m.address == addr:
                return m
        raise KeyError(f"unknown module address {spec!r}; use --tx/--rx for custom ids")
    except ValueError:
        pass
    hits = [m for m in MODULES if s in m.name.lower()]
    if len(hits) == 1:
        return hits[0]
    raise KeyError(f"cannot resolve module {spec!r}" + (" (ambiguous)" if hits else ""))


IDENT_DIDS: Dict[int, str] = {
    0xF190: "VIN",
    0xF187: "Spare part number",
    0xF189: "Software version",
    0xF191: "Hardware number",
    0xF18C: "ECU serial number",
    0xF19E: "ODX file identifier",
}

NRC_NAMES: Dict[int, str] = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceededNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x72: "generalProgrammingFailure",
    0x78: "requestCorrectlyReceived-ResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}

STATUS_BITS = [
    "testFailed",
    "testFailedThisOperationCycle",
    "pending",
    "confirmed",
    "testNotCompletedSinceClear",
    "testFailedSinceClear",
    "testNotCompletedThisCycle",
    "warningIndicatorRequested",
]


def sae_code(b0: int, b1: int) -> str:
    """Two bytes -> SAE J2012 code, e.g. 0x0300 -> P0300."""
    letter = "PCBU"[b0 >> 6]
    return f"{letter}{(b0 >> 4) & 3}{b0 & 0xF:X}{b1 >> 4:X}{b1 & 0xF:X}"


def vag_number(sae: str) -> Optional[int]:
    """VAG 5-digit fault number for powertrain codes: P0300 -> 16684 (16384 + 300).

    Codes of other groups (B/C/U) use different numbering and return None.
    """
    if len(sae) == 5 and sae[0] == "P" and sae[1:].isdigit():
        return 16384 + int(sae[1:])
    return None


def sae_from_vag(n: int) -> Optional[str]:
    """Inverse of vag_number for powertrain codes."""
    if 16384 <= n < 16384 + 4000:
        return f"P{n - 16384:04d}"
    return None


@dataclass
class Dtc:
    raw: int  # 24-bit UDS DTC
    status: int = 0

    @property
    def sae(self) -> str:
        return sae_code((self.raw >> 16) & 0xFF, (self.raw >> 8) & 0xFF)

    @property
    def ftb(self) -> int:
        return self.raw & 0xFF

    @property
    def vag(self) -> Optional[int]:
        return vag_number(self.sae)

    @property
    def status_flags(self) -> List[str]:
        return [n for i, n in enumerate(STATUS_BITS) if self.status & (1 << i)]

    def __str__(self) -> str:
        vag = self.vag
        vag_s = f"{vag:05d}" if vag is not None else "-----"
        return f"{vag_s}  {self.sae}-{self.ftb:02X}  status=0x{self.status:02X}"


@dataclass
class KwpDtc:
    """Fault code as reported by KWP2000: 16-bit VAG number plus a status byte."""

    code: int
    status: int = 0

    @property
    def sae(self) -> Optional[str]:
        return sae_from_vag(self.code)

    @property
    def vag(self) -> int:
        return self.code

    @property
    def status_flags(self) -> List[str]:
        return []  # KWP status bytes are ECU specific

    def __str__(self) -> str:
        return f"{self.code:05d}  {self.sae or '-----'}  status=0x{self.status:02X}"
