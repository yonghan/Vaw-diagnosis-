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

Not supported: K-line/KKL cables, ELM327 (planned), and pre-~2008 VW TP 2.0 / KWP2000
cars.

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
number is derived only for generic powertrain codes (P0xxx/P2xxx); others show `-----`.

## Try it without a car

`-i sim` runs a simulated set of ECUs (engine, brakes, instrument cluster, gateway)
on an in-process virtual bus:

```
vwdiag -i sim scan
vwdiag -i sim live -n 5
```

## Development

```
pip install -e .[dev] && pytest
```

Layout: `can.py` (SocketCAN / virtual bus), `isotp.py`, `uds.py`, `obd.py`,
`vag.py` (modules, DTC formatting), `sim.py`, `cli.py`.

## Roadmap

Measuring blocks / adaptation / coding, security access, long coding helper,
ELM327 and TP 2.0 support, DoIP for newer MEB cars.
