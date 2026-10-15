import pytest

from vwdiag import cli
from vwdiag.can import VirtualNetwork
from vwdiag.isotp import IsoTp
from vwdiag.obd import ObdClient, decode_dtcs, resolve_pid
from vwdiag.sim import Simulator
from vwdiag.uds import UdsClient, UdsNegativeResponse
from vwdiag.vag import Dtc, find_module, sae_code, vag_number


@pytest.fixture
def sim():
    s = Simulator()
    yield s
    s.close()


def channel(sim, tx, rx):
    return IsoTp(sim.bus(), tx, rx, timeout=1.0)


def test_isotp_multiframe_roundtrip():
    net = VirtualNetwork()
    a, b = IsoTp(net.bus(), 0x100, 0x200), IsoTp(net.bus(), 0x200, 0x100)
    import threading

    got = []
    t = threading.Thread(target=lambda: got.append(b.recv(2)))
    t.start()
    msg = bytes(range(200))
    a.send(msg)
    t.join()
    assert got == [msg]


def test_isotp_block_size():
    net = VirtualNetwork()
    a = IsoTp(net.bus(), 0x100, 0x200)
    b = IsoTp(net.bus(), 0x200, 0x100, block_size=3, stmin=1)
    import threading

    got = []
    t = threading.Thread(target=lambda: got.append(b.recv(2)))
    t.start()
    msg = bytes(range(100))
    a.send(msg)
    t.join()
    assert got == [msg]


def test_sae_and_vag_numbers():
    assert sae_code(0x03, 0x00) == "P0300"
    assert sae_code(0xC1, 0x21) == "U0121"
    assert vag_number("P0300") == 16684
    assert vag_number("P1234") is None
    assert "16684" in str(Dtc(0x030000, 0x2F))


def test_find_module():
    assert find_module("03").name == "Brakes"
    assert find_module("engine").address == 1
    with pytest.raises(KeyError):
        find_module("zz")


def test_uds_ident_and_dtc(sim):
    uds = UdsClient(channel(sim, 0x7E0, 0x7E8))
    assert uds.read_did(0xF190) == Simulator.VIN
    assert [d.sae for d in uds.read_dtcs()] == ["P0300", "P0134"]
    with pytest.raises(UdsNegativeResponse) as e:
        uds.read_did(0x1234)
    assert e.value.nrc == 0x31
    uds.clear_dtcs()
    assert uds.read_dtcs() == []


def test_obd(sim):
    obd = ObdClient(channel(sim, 0x7E0, 0x7E8))
    assert 0x0C in obd.supported_pids()
    assert 700 < obd.read_pid(resolve_pid("rpm")) < 1000
    assert obd.vin() == Simulator.VIN.decode()
    assert obd.stored_dtcs() == ["P0300"]
    assert obd.pending_dtcs() == ["P0134"]


def test_decode_dtcs_skips_padding():
    assert decode_dtcs(bytes([0x03, 0x00, 0, 0])) == ["P0300"]


def test_cli_scan_and_dtc(capsys):
    assert cli.main(["-i", "sim", "scan"]) == 0
    out = capsys.readouterr().out
    assert "Brakes" in out and "4 module(s) responded" in out
    assert cli.main(["-i", "sim", "dtc"]) == 0
    assert "P0300" in capsys.readouterr().out


def test_cli_clear_needs_confirmation(capsys):
    assert cli.main(["-i", "sim", "clear"]) == 2  # stdin is not a tty under pytest


def test_cli_unknown_module(capsys):
    assert cli.main(["-i", "sim", "-m", "99", "dtc"]) == 1
