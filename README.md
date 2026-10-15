# vwdiag

An open-source, Linux command-line diagnostic tool for Volkswagen Group vehicles
(VW, Audi, Škoda, SEAT/Cupra) in the spirit of VCDS. It speaks **UDS (ISO 14229)**
and **OBD-II (SAE J1979)** over **ISO-TP (ISO 15765-2)** on CAN, using Linux
**SocketCAN**. Pure Python, no runtime dependencies.

> Independent project, not affiliated with Ross-Tech or Volkswagen AG.
> Use at your own risk. Writing to ECUs can have side effects; destructive
> commands (`clear`, `reset`) ask for confirmation.

## Hardware

Any SocketCAN-capable CAN adapter wired to the OBD-II port (pins 6 = CAN-H,
14 = CAN-L, 4/5 = ground, 500 kbit/s; on newer cars behind the gateway you may
need an SGW bypass/diagnostic access):

```
sudo ip link set can0 up type can bitrate 500000        # PEAK, CANable (candlelight), MCP2515, ...
sudo slcand -o -c -s6 /dev/ttyACM0 slcan0 && sudo ip link set slcan0 up   # serial/slcan adapters
```

Not supported yet: K-line/KKL cables, ELM327, and proprietary adapters such as
HEX-V2 (and clones), which speak their own USB protocol rather than SocketCAN.

## Install & use

```
pip install -e .            # or: python -m vwdiag ...
vwdiag -i can0 scan                  # find modules, show part numbers & DTC counts
vwdiag -i can0 -m engine ident       # identification (VIN, part no., SW/HW versions)
vwdiag -i can0 -m 03 dtc             # fault codes (VAG number, SAE code, status flags)
vwdiag -i can0 -m 03 clear           # clear fault codes (asks first)
vwdiag -i can0 live rpm speed coolant -n 20 --csv    # OBD-II live data
vwdiag -i can0 live --list           # PIDs the engine ECU supports
vwdiag -i can0 obd vin | dtc | pending | clear
vwdiag -i can0 -m 17 did F187        # read any data identifier
vwdiag -i can0 -m 01 session extended
vwdiag -i can0 -m 01 reset --type soft
vwdiag -i can0 raw 22F190            # raw request / response
vwdiag -i can0 sniff                 # candump-style monitor
vwdiag modules                       # known address -> CAN id table
```

Module addresses follow the VAG convention (01 Engine, 03 Brakes, 17 Instruments,
19 Gateway, ...). The CAN id table in `vwdiag/vag.py` reflects typical MQB-era
cars; override per car with `--tx 713 --rx 77D`.

Fault codes are shown as `VAG-number  SAE-code-FTB  status`. The 5-digit VAG
number is derived only for powertrain codes (P0xxx-P3xxx, 16384 + the code number); others show `-----`.

## Older cars: TP 2.0 + KWP2000

Cars up to roughly 2012 (e.g. a 2010 Audi Q5) talk **KWP2000 over VW TP 2.0** to most
modules; only the engine ECU also answers plain OBD-II on `7E0`. Select it with
`-p tp20`, or leave the default `-p auto`, which tries TP 2.0 first and falls back to UDS:

```
vwdiag -i can0 scan                  # shows [KWP2000/TP2.0] or [UDS/ISO-TP] per module
vwdiag -i can0 -m 15 dtc             # airbag faults over KWP2000 (5-digit VAG number + P-code if any)
vwdiag -i can0 -m 17 ident
vwdiag -i can0 -m 01 raw 1A9B        # any KWP request; the channel is opened for you
```

**Status:** implemented from public protocol documentation and tested only against the
built-in simulator (`-i sim-tp20`), not yet against a real car. Please report what
differs. Channel-setup ids, timing and ident record layout are the likely places.
KWP ident records are shown as their printable text fields, not parsed.

## Try it without a car

`-i sim` runs a simulated UDS car (engine, brakes, instrument cluster, gateway) and
`-i sim-tp20` a 2010-style car (KWP2000 over TP 2.0) on an in-process virtual bus:

```
vwdiag -i sim scan
vwdiag -i sim live -n 5
vwdiag -i sim-tp20 -m 15 dtc
```

## Development

```
pip install -e .[dev] && pytest
```

Layout: `can.py` (SocketCAN / virtual bus), `isotp.py`, `tp20.py`, `uds.py` (UDS + KWP2000
clients), `session.py` (protocol-agnostic sessions), `obd.py`, `vag.py` (modules, DTC
formatting), `sim.py`, `cli.py`.

## Roadmap

Measuring blocks (KWP `21`), adaptation / coding, security access, a HEX-V2 USB
backend, ELM327, DoIP for newer MEB cars.
