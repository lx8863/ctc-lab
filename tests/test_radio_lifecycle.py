"""Verify cleanup and hardware configuration without opening an actual device."""
from types import SimpleNamespace
import sys
import pytest
import ctc_lab.radio as radio


class FakeSDR:
    def __init__(self,uri):
        self.uri = uri
        self._ctx = SimpleNamespace(set_timeout=lambda milliseconds:None)
        self.closed = []

    def tx_destroy_buffer(self):
        self.closed.append("tx")

    def rx_destroy_buffer(self):
        self.closed.append("rx")


def test_rf_is_not_digital_loopback_and_cleanup_on_failure(monkeypatch):
    sdr = FakeSDR("fake:")
    monkeypatch.setattr(radio,"_native",lambda:None)
    monkeypatch.setitem(sys.modules,"adi",SimpleNamespace(ad9361=lambda uri:sdr))
    with pytest.raises(RuntimeError,match="deliberate"):
        with radio.Radio("fake:",8_000_000,2_425_000_000) as device:
            assert device.sdr.loopback == 0
            assert device.sdr.tx_cyclic_buffer is True
            assert device.sdr.tx_hardwaregain_chan0 == -40
            raise RuntimeError("deliberate test failure")
    assert sdr.closed == ["tx","rx"]
