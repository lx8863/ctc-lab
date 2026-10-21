import numpy as np
import pytest

from ctc_lab.ieee802154 import (CHIPS, build_mac, fcs, modulate, parse_mac,
                              ppdu, octets_to_symbols, channel_frequency)
from ctc_lab.receiver import decode
from ctc_lab.channel import impair


def test_external_crc_check_value():
    # Published CRC-16/KERMIT check, independent of local TX/RX round-trip.
    assert fcs(b"123456789") == 0x2189
    assert fcs(b"123456789"+bytes.fromhex("8921")) == 0


def test_external_chip_and_header_conventions():
    # TI SWRU191F table 23-2 and low-nibble-first symbol transmission.
    assert "".join(map(str, CHIPS[0])) == "11011001110000110101001000101110"
    assert "".join(map(str, CHIPS[15])) == "11001001011000000111011110111000"
    assert octets_to_symbols(bytes.fromhex("a701")).tolist() == [7,10,1,0]
    frame = build_mac(b"x", 7)
    assert frame[:9].hex() == "418807feca ffff0100".replace(" ","")
    assert ppdu(frame)[:6] == bytes.fromhex("00000000a70c")
    assert parse_mac(frame)["payload_hex"] == "78"
    assert channel_frequency(11) == 2_405_000_000
    assert channel_frequency(26) == 2_480_000_000


def test_oqpsk_pulse_and_stagger_against_analytic_samples():
    from ctc_lab.ieee802154 import modulate_symbols
    wave = modulate_symbols(np.array([0]),8_000_000)
    # C0..C3=1,1,0,1; alternating branch peaks at Tc,2Tc,3Tc,4Tc.
    np.testing.assert_allclose(wave[[4,8,12,16]],[1,1j,-1,1j],atol=1e-7)


@pytest.mark.parametrize("sample_rate,cfo,snr,delay,clock", [
    (4_000_000,0,30,31,0),
    (8_000_000,37_500,20,137.3,0),
    (8_000_000,-72_000,18,519.6,0),
    (8_000_000,12_000,20,73.2,30),
])
def test_independent_acquisition(sample_rate,cfo,snr,delay,clock):
    rng = np.random.default_rng(832)
    payload = rng.bytes(22)
    frame = build_mac(payload,193)
    iq = np.pad(modulate(frame,sample_rate),(100,500))
    captured = impair(iq,sample_rate,snr_db=snr,cfo_hz=cfo,
                      delay_samples=delay,clock_ppm=clock)
    frames = decode(captured,sample_rate)
    good = [f for f in frames if f["fcs_ok"]]
    assert len(good) == 1, frames
    assert good[0]["psdu_hex"] == frame.hex()
    assert abs(good[0]["cfo_hz"]-cfo) < 1500


def test_corrupt_fcs_rejected():
    frame = bytearray(build_mac(b"deliberate corruption"))
    frame[-1] ^= 0x01
    iq = np.pad(modulate(bytes(frame)),(100,500))
    frames = decode(iq)
    assert frames
    assert all(not frame["fcs_ok"] for frame in frames)


def test_noise_does_not_make_valid_packets():
    rng = np.random.default_rng(7523)
    iq = rng.standard_normal(40000)+1j*rng.standard_normal(40000)
    assert decode(iq) == []


def test_multiple_frames_and_repeated_sequences():
    frames = [build_mac(b"first",255),build_mac(b"second",0),build_mac(b"third",0)]
    parts = [np.zeros(700,dtype=complex)]
    for frame in frames:
        parts.extend((modulate(frame),np.zeros(5000,dtype=complex)))
    captured = impair(np.concatenate(parts),8_000_000,cfo_hz=24500,snr_db=22)
    received = [f for f in decode(captured) if f["fcs_ok"]]
    assert [f["psdu_hex"] for f in received] == [f.hex() for f in frames]


def test_truncated_capture_not_accepted():
    wave = modulate(build_mac(bytes(range(80))))
    assert not any(f["fcs_ok"] for f in decode(wave[:len(wave)//2]))


def test_length_limits():
    assert len(build_mac(bytes(116))) == 127
    with pytest.raises(ValueError):
        build_mac(bytes(117))
    with pytest.raises(ValueError):
        modulate(build_mac(b"x"),3_000_000)
