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
    assert vag_number("U0121") is None
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


# ---- TP 2.0 / KWP2000 -------------------------------------------------------
import threading

from vwdiag.can import Frame
from vwdiag.session import connect
from vwdiag.tp20 import Tp20Channel
from vwdiag.vag import KwpDtc, sae_from_vag


@pytest.fixture
def q5():
    s = Simulator("q5")
    yield s
    s.close()


def test_tp20_wire_format():
    """Setup request and data framing match the documented byte layout."""
    net = VirtualNetwork()
    tester, peer = net.bus(), net.bus()
    seen = []

    def ecu():
        f = peer.recv(2)
        seen.append(f)
        peer.send(Frame(0x201, bytes([0x00, 0xD0, 0x00, 0x03, 0x40, 0x07, 0x01])))
        f = peer.recv(2)  # A0 parameters
        seen.append(f)
        peer.send(Frame(0x300, bytes([0xA1, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF])))
        f = peer.recv(2)  # first data frame
        seen.append(f)
        peer.send(Frame(0x300, bytes([0xB1])))

    t = threading.Thread(target=ecu)
    t.start()
    ch = Tp20Channel.open(tester, 0x01)
    assert (ch.tx_id, ch.rx_id) == (0x740, 0x300)
    ch.send(bytes([0x10, 0x89]))
    t.join()
    assert seen[0].arbitration_id == 0x200 and seen[0].data == bytes([1, 0xC0, 0, 0x10, 0, 3, 1])
    assert seen[1].arbitration_id == 0x740 and seen[1].data[:1] == b"\xA0"
    assert seen[2].data == bytes([0x10, 0x00, 0x02, 0x10, 0x89])


def test_tp20_multipacket_both_ways(q5):
    ch = Tp20Channel.open(q5.bus(), 0x15)
    ch.send(bytes([0x1A, 0x9B]))
    resp = ch.recv(1)
    assert resp.startswith(b"\x5A\x9B8K0959655C") and len(resp) > 20
    ch.ping()
    ch.close()
    assert ch.closed


def test_kwp_via_connect(q5):
    d = connect(q5.bus(), find_module("15"), "auto")
    assert d.protocol.startswith("KWP")
    codes = d.dtcs()
    assert [c.vag for c in codes] == [1234] and codes[0].sae is None
    assert "8K0959655C" in dict(d.ident())["ECU identification"]
    d.clear()
    assert d.dtcs() == []
    d.close()


def test_auto_prefers_tp20_then_falls_back_to_uds(q5, sim):
    assert connect(q5.bus(), find_module("01"), "auto").protocol.startswith("KWP")
    assert connect(sim.bus(), find_module("03"), "auto").protocol.startswith("UDS")


def test_tp20_no_module_times_out(q5):
    from vwdiag.uds import UdsTimeout

    with pytest.raises(UdsTimeout):
        connect(q5.bus(), find_module("44"), "tp20", timeout=0.2)


def test_vag_number_mapping():
    assert vag_number("P1579") == 17963 and sae_from_vag(17963) == "P1579"
    assert sae_from_vag(1234) is None
    assert "16684" in str(KwpDtc(16684, 0x2F)) and "P0300" in str(KwpDtc(16684, 0x2F))


def test_cli_tp20_scan(capsys):
    assert cli.main(["-i", "sim-tp20", "scan"]) == 0
    out = capsys.readouterr().out
    assert "KWP2000/TP2.0" in out and "5 module(s)" not in out
    assert cli.main(["-i", "sim-tp20", "-m", "15", "dtc"]) == 0
    assert "01234" in capsys.readouterr().out
