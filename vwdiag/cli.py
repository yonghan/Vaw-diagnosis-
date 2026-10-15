"""vwdiag command line interface."""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional, Tuple

from . import __version__
from .can import Bus, SocketCanBus
from .isotp import IsoTp, IsoTpError
from .obd import PIDS, ObdClient, resolve_pid
from .session import connect, printable
from .tp20 import Tp20Error
from .uds import UdsError, UdsTimeout
from .vag import MODULES, Module, find_module


def _hex(s: str) -> int:
    return int(s, 16)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vwdiag", description="VW Group diagnostics over CAN (UDS / OBD-II)")
    p.add_argument("--version", action="version", version=f"vwdiag {__version__}")
    p.add_argument("-i", "--interface", default="can0",
                   help="SocketCAN interface (can0, vcan0, slcan0 ...), or a built-in simulator: "
                        "'sim' (UDS car) / 'sim-tp20' (2010-style TP 2.0 car)")
    p.add_argument("-m", "--module", default="01", help="module address (01) or name (engine); default 01")
    p.add_argument("-p", "--protocol", choices=["auto", "uds", "tp20"], default="auto",
                   help="uds = UDS/ISO-TP, tp20 = KWP2000 over VW TP 2.0 (older cars), auto = try both")
    p.add_argument("--tx", type=_hex, help="override tester->ECU CAN id (hex)")
    p.add_argument("--rx", type=_hex, help="override ECU->tester CAN id (hex)")
    p.add_argument("--timeout", type=float, default=1.0, help="response timeout in seconds")
    p.add_argument("--padding", type=_hex, default=0xAA, help="CAN frame padding byte (hex)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("modules", help="list known control modules and their CAN ids")
    sub.add_parser("scan", help="auto-scan: find responding modules and show identification")
    sub.add_parser("ident", help="read identification data of one module")
    sub.add_parser("dtc", help="read fault codes of one module")
    c = sub.add_parser("clear", help="clear fault codes of one module")
    c.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    r = sub.add_parser("reset", help="ECU reset")
    r.add_argument("--type", choices=["hard", "key-off", "soft"], default="hard")
    r.add_argument("-y", "--yes", action="store_true")
    s = sub.add_parser("session", help="open a diagnostic session")
    s.add_argument("kind", choices=["default", "extended", "programming"], nargs="?", default="extended")
    d = sub.add_parser("did", help="read a data identifier (UDS service 22)")
    d.add_argument("did", type=_hex, help="DID in hex, e.g. F190")
    lv = sub.add_parser("live", help="live OBD-II data from the engine ECU")
    lv.add_argument("pids", nargs="*", help="PID names or hex ids (default: rpm speed coolant load)")
    lv.add_argument("-n", "--count", type=int, default=0, help="stop after N samples (0 = until Ctrl-C)")
    lv.add_argument("--interval", type=float, default=0.5)
    lv.add_argument("--csv", action="store_true", help="CSV output")
    lv.add_argument("--list", action="store_true", help="list supported PIDs and exit")
    o = sub.add_parser("obd", help="generic OBD-II emissions data")
    o.add_argument("what", choices=["dtc", "pending", "clear", "vin"])
    o.add_argument("-y", "--yes", action="store_true")
    raw = sub.add_parser("raw", help="send a raw diagnostic request (hex bytes) and print the response")
    raw.add_argument("data", help="e.g. 22F190")
    sn = sub.add_parser("sniff", help="print all CAN frames (candump style)")
    sn.add_argument("-n", "--count", type=int, default=0)
    return p


def open_bus(name: str) -> Tuple[Bus, Optional[object]]:
    if name in ("sim", "sim-tp20"):
        from .sim import Simulator

        sim = Simulator("q5" if name == "sim-tp20" else "mqb")
        return sim.bus(), sim
    return SocketCanBus(name), None


def _diag(args, module: Optional[Module] = None):
    m = module or find_module(args.module)
    d = connect(args.bus, m, args.protocol, timeout=args.timeout, padding=args.padding,
                tx=args.tx, rx=args.rx)
    return d, m


def _confirm(args, what: str) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("refusing without --yes on non-interactive input", file=sys.stderr)
        return False
    return input(f"{what} Proceed? [y/N] ").strip().lower() == "y"


def cmd_modules(args) -> int:
    print("Addr  Name               TX     RX")
    for m in MODULES:
        print(f"{m.address:02X}    {m.name:<18} {m.tx_id:03X}    {m.rx_id:03X}")
    return 0


def cmd_scan(args) -> int:
    found = 0
    for m in MODULES:
        try:
            d = connect(args.bus, m, args.protocol, timeout=args.timeout, padding=args.padding)
        except (UdsError, IsoTpError, Tp20Error):
            print(f"{m.address:02X} {m.name:<18} -- no response")
            continue
        try:
            found += 1
            info = d.ident()
            try:
                n = len(d.dtcs())
                dtc = f"{n} DTC" if n else "no DTC"
            except (UdsError, Tp20Error):
                dtc = "DTC n/a"
            summary = "  ".join(f"{k}={v}" for k, v in info[:2]) or "(no ident)"
            print(f"{m.address:02X} {m.name:<18} OK  [{d.protocol}]  {summary}  {dtc}")
        except (UdsError, Tp20Error) as e:
            print(f"{m.address:02X} {m.name:<18} OK  [{d.protocol}]  error: {e}")
        finally:
            d.close()
    print(f"\n{found} module(s) responded")
    return 0 if found else 1


def cmd_ident(args) -> int:
    d, m = _diag(args)
    try:
        print(f"Module {m.address:02X} {m.name}  [{d.protocol}]")
        items = d.ident()
        for k, v in items:
            print(f"  {k:<20} {v}")
        return 0 if items else 1
    finally:
        d.close()


def cmd_dtc(args) -> int:
    d, m = _diag(args)
    try:
        dtcs = d.dtcs()
        print(f"Module {m.address:02X} {m.name} [{d.protocol}]: {len(dtcs)} fault code(s)")
        for c in dtcs:
            print(f"  {c}")
            if c.status_flags:
                print(f"      {', '.join(c.status_flags)}")
        return 0
    finally:
        d.close()


def cmd_clear(args) -> int:
    d, m = _diag(args)
    try:
        if not _confirm(args, f"This erases all fault codes in {m.name}."):
            return 2
        d.clear()
        print("Fault codes cleared.")
        return 0
    finally:
        d.close()


def cmd_reset(args) -> int:
    d, m = _diag(args)
    try:
        if not _confirm(args, f"This resets {m.name}."):
            return 2
        d.reset({"hard": 1, "key-off": 2, "soft": 3}[args.type])
        print("Reset accepted.")
        return 0
    finally:
        d.close()


def cmd_session(args) -> int:
    d, m = _diag(args)
    try:
        d.session(args.kind)
        print(f"{m.name}: {args.kind} session accepted.")
        return 0
    finally:
        d.close()


def cmd_did(args) -> int:
    d, _ = _diag(args)
    try:
        data = d.did(args.did)
        print(f"{args.did:04X}: {data.hex().upper()}  {printable(data)!r}")
        return 0
    finally:
        d.close()


def _engine(args) -> ObdClient:
    m = find_module(args.module)  # engine ECU 7E0/7E8 unless -m/--tx/--rx given
    tx = args.tx if args.tx is not None else m.tx_id
    rx = args.rx if args.rx is not None else m.rx_id
    return ObdClient(IsoTp(args.bus, tx, rx, padding=args.padding, timeout=args.timeout))


def cmd_live(args) -> int:
    obd = _engine(args)
    if args.list:
        for n in obd.supported_pids():
            if n % 0x20 == 0:
                continue  # range markers, not data PIDs
            p = PIDS.get(n)
            print(f"{n:02X}  {p.key:<10} {p.name} [{p.unit}]" if p else f"{n:02X}  (no decoder)")
        return 0
    pids = [resolve_pid(s) for s in (args.pids or ["rpm", "speed", "coolant", "load"])]
    if args.csv:
        print("time," + ",".join(p.key for p in pids))
    else:
        print("  ".join(f"{p.key}[{p.unit}]".rjust(14) for p in pids))
    n, t0 = 0, time.monotonic()
    try:
        while not args.count or n < args.count:
            vals = [obd.read_pid(p) for p in pids]
            if args.csv:
                print(f"{time.monotonic() - t0:.2f}," + ",".join(f"{v:.2f}" for v in vals))
            else:
                print("  ".join(f"{v:14.1f}" for v in vals))
            n += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_obd(args) -> int:
    obd = _engine(args)
    if args.what == "vin":
        print(obd.vin())
    elif args.what in ("dtc", "pending"):
        codes = obd.stored_dtcs() if args.what == "dtc" else obd.pending_dtcs()
        print("\n".join(codes) if codes else "no codes")
    else:
        if not _confirm(args, "This clears emissions DTCs and readiness monitors."):
            return 2
        obd.clear_dtcs()
        print("Cleared.")
    return 0


def cmd_raw(args) -> int:
    d, _ = _diag(args)
    try:
        tp = d.tp
        payload = bytes.fromhex(args.data.replace(" ", ""))
        tp.flush()
        tp.send(payload)
        deadline = time.monotonic() + args.timeout
        while True:
            resp = tp.recv(max(deadline - time.monotonic(), 0))
            if resp is None:
                print("no response", file=sys.stderr)
                return 1
            print(resp.hex().upper())
            if not (resp[0] == 0x7F and len(resp) >= 3 and resp[2] == 0x78):
                return 0
            deadline = time.monotonic() + 5.0
    finally:
        d.close()


def cmd_sniff(args) -> int:
    n = 0
    try:
        while not args.count or n < args.count:
            f = args.bus.recv(1.0)
            if f:
                print(f"{time.time():.6f} {args.interface} {f}")
                n += 1
    except KeyboardInterrupt:
        pass
    return 0


COMMANDS = {k[4:]: v for k, v in globals().items() if k.startswith("cmd_")}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "modules":
        return cmd_modules(args)
    try:
        args.bus, sim = open_bus(args.interface)
    except OSError as e:
        print(f"cannot open {args.interface}: {e}", file=sys.stderr)
        return 1
    try:
        return COMMANDS[args.cmd](args)
    except (UdsError, IsoTpError, Tp20Error, KeyError, ValueError) as e:
        print(f"error: {e.args[0] if isinstance(e, KeyError) else e}", file=sys.stderr)
        return 1
    finally:
        args.bus.close()
        if sim:
            sim.close()


if __name__ == "__main__":
    sys.exit(main())
