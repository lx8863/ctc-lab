"""Packet fields checked against the Nordic layout and independent CRC division."""
import binascii
import numpy as np
import pytest
from ctc_lab import esb
from ctc_lab.esb_receiver import decode
from ctc_lab.channel import impair


def reference_crc(bitstring):
    value = (int(bitstring, 2) << 16) ^ (0xffff << len(bitstring))
    while value.bit_length() > 16:
        value ^= 0x11021 << (value.bit_length()-17)
    return f'{value:016b}'


def test_crc_reference_check_and_unpadded_packet_layout():
    assert esb.crc16(esb.bits_msb(b'123456789')) == binascii.crc_hqx(b'123456789',0xffff) == 0x29b1
    for pid in range(4):
        payload = b'\x00\xffA'
        body = esb.ADDRESS.hex()
        bits = ''.join(f'{x:08b}' for x in bytes.fromhex(body))
        bits += f'{len(payload):06b}{pid:02b}1'
        bits += ''.join(f'{x:08b}' for x in payload)
        expected = '10101010'+bits+reference_crc(bits)
        assert ''.join(map(str, esb.packet_bits(payload,pid))) == expected


@pytest.mark.parametrize('payload', [b'\x00',b'\xff',bytes(range(32)),b'\x55'*32,'你好 ESB'.encode()])
@pytest.mark.parametrize('cfo', [-30000, 30000])
def test_blind_decode_varied_payloads_with_impairments(payload,cfo):
    iq, _ = esb.burst(payload)
    captured = impair(iq[:24000],8_000_000,snr_db=18,cfo_hz=cfo,
                      clock_ppm=20,delay_samples=173.3,seed=42)
    rows = decode(captured)
    assert any(row['payload_hex']==payload.hex() and row['crc_ok'] for row in rows)
    assert all(row['no_ack'] for row in rows)


def test_crc_corruption_and_wrong_address_are_rejected():
    bits = esb.packet_bits(b'test',pid=2)
    bits[-1] ^= 1
    iq = np.pad(esb.modulate(bits),(8000,8000))
    assert decode(iq)==[]
    bits = esb.packet_bits(b'test',address=bytes.fromhex('d391b7a6c4'))
    assert decode(np.pad(esb.modulate(bits),(8000,8000)))==[]


def test_low_first_address_bit_preamble_and_packet_id():
    address = bytes.fromhex('5391b7a6c5')
    bits = esb.packet_bits(b'abc',pid=3,address=address)
    assert np.packbits(bits[:8]).tobytes()==b'\x55'
    frames = decode(np.pad(esb.modulate(bits),(8000,8000)),address=address)
    assert frames and frames[0]['pid']==3 and frames[0]['payload_hex']==b'abc'.hex()
