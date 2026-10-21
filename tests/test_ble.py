import numpy as np
import pytest
from ctc_lab.ble import (bits_lsb,crc,whiten,wire_packet,advertising_pdu,
                        advertising_data,gfsk,frequency)
from ctc_lab.ble_receiver import decode_advertisements
from ctc_lab.channel import impair


def test_bluetooth_sig_official_legacy_advertising_vector():
    # Core 6.0 Vol 6 Part C 4.2.1. Expected bits listed in transmission order.
    pdu = advertising_pdu(bytes.fromhex("010203"),"C1:A2:A3:A4:A5:A6")
    assert pdu.hex()=="4209a6a5a4a3a2c1010203"
    assert crc(pdu).hex()=="adb4eb"
    expected = ("01010101 01101011 01111101 10010001 01110001 "
        "00101001 00110011 01000111 10100001 10111111 10111110 "
        "11000010 01110010 01011000 11100101 00110101 11110111 "
        "11110011 10100101").replace(" ","")
    assert "".join(map(str,bits_lsb(wire_packet(pdu,38))))==expected


@pytest.mark.parametrize("channel",[37,38,39])
def test_whitening_is_invertible_and_channel_specific(channel):
    raw = bytes(range(48))
    assert whiten(whiten(raw,channel),channel)==raw
    assert whiten(raw,channel)!=whiten(raw,(channel+1)%40)


def test_actual_gfsk_frequency_and_envelope():
    fs = 8_000_000
    for packet,want in ((bytes([255])*8,250_000),(bytes(8),-250_000)):
        iq = gfsk(packet,fs)
        measured = np.angle(iq[1:]*np.conj(iq[:-1]))*fs/(2*np.pi)
        np.testing.assert_allclose(measured,want,atol=1)
        np.testing.assert_allclose(np.abs(iq),1,atol=1e-6)


@pytest.mark.parametrize("channel",[37,38,39])
def test_blind_decode_gfsk_with_noise_and_cfo(channel):
    pdu = advertising_pdu(advertising_data())
    wave = np.pad(gfsk(wire_packet(pdu,channel)),(700,12000))
    captured = impair(wave,8_000_000,snr_db=25,cfo_hz=42000,
                      phase=1.23,delay_samples=37.3)
    decoded = decode_advertisements(captured,channel)
    assert len(decoded)==1
    assert decoded[0]["pdu_hex"]==pdu.hex()
    assert decoded[0]["name"]=="SDR-BLE-TEST"
    assert decoded[0]["test_marker_hex"]=="12345678"


def test_corrupted_ble_crc_and_wrong_channel_rejected():
    pdu = advertising_pdu(advertising_data())
    packet = bytearray(wire_packet(pdu,37))
    packet[-1] ^= 0x04
    iq = np.pad(gfsk(bytes(packet)),(1000,10000))
    assert decode_advertisements(iq,37)==[]
    clean = np.pad(gfsk(wire_packet(pdu,37)),(1000,10000))
    assert decode_advertisements(clean,38)==[]


def test_advertising_limits():
    with pytest.raises(ValueError):
        advertising_data("x"*28)
    with pytest.raises(ValueError):
        advertising_pdu(bytes(32))
    with pytest.raises(ValueError):
        advertising_pdu(b"x","00:11:22:33:44:55")
    assert frequency(37)==2_402_000_000
