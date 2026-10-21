"""ESB 1 Mbps, 5-byte address, dynamic length, NO_ACK and CRC16.

Packet layout follows nRF24L01+ Product Specification v1.0 section 7.3.
This is a one-way SDR implementation, not an ACK/retry protocol engine.
"""
from __future__ import annotations
import numpy as np

ADDRESS = bytes.fromhex('d391b7a6c5')  # Bytes in on-air order, MSB first.
FREQUENCY = 2_440_000_000  # RF_CH=40
BIT_RATE = 1_000_000
DEVIATION = 160_000


def bits_msb(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder='big')


def integer_bits(value: int, width: int) -> np.ndarray:
    return np.array([(value >> k) & 1 for k in range(width-1, -1, -1)], dtype=np.uint8)


def crc16(bits) -> int:
    """MSB-first 0x1021, init FFFF, no xorout. Includes unpadded 9-bit PCF."""
    state = 0xFFFF
    for bit in bits:
        feedback = (state >> 15) ^ int(bit)
        state = (state << 1) & 0xFFFF
        if feedback:
            state ^= 0x1021
    return state


def packet_bits(payload: bytes, pid: int = 0, address: bytes = ADDRESS) -> np.ndarray:
    if not 1 <= len(payload) <= 32 or not 0 <= pid <= 3 or len(address) != 5:
        raise ValueError('ESB requires 1..32 payload bytes, PID 0..3 and a 5-byte address')
    pcf = integer_bits((len(payload) << 3) | (pid << 1) | 1, 9)
    body = np.concatenate((bits_msb(address), pcf, bits_msb(payload)))
    preamble = bits_msb(bytes([0xAA if address[0] & 0x80 else 0x55]))
    return np.concatenate((preamble, body, integer_bits(crc16(body), 16)))


def modulate(bits: np.ndarray, sample_rate: int = 8_000_000) -> np.ndarray:
    if sample_rate % BIT_RATE or sample_rate < 4_000_000:
        raise ValueError('ESB 1M requires >=4 integer samples per bit')
    bits = np.asarray(bits)
    if bits.ndim != 1 or not len(bits) or not np.isin(bits, (0,1)).all():
        raise ValueError('Expected a nonempty binary vector')
    spb = sample_rate // BIT_RATE
    nrz = np.repeat(bits.astype(float)*2-1, spb)
    t = np.arange(-2*spb, 2*spb+1)/spb
    kernel = np.exp(-2*np.pi**2*.5**2*t*t/np.log(2))
    kernel /= kernel.sum()
    shaped = np.convolve(np.pad(nrz, (2*spb,2*spb), mode='edge'), kernel, mode='valid')
    return np.exp(1j*np.cumsum(shaped)*(2*np.pi*DEVIATION/sample_rate)).astype(np.complex64)


def burst(payload: bytes, sample_rate: int = 8_000_000):
    bits = packet_bits(payload)
    wave = modulate(bits, sample_rate)
    iq = np.zeros(sample_rate//100, dtype=np.complex64)  # Cyclic 10 ms schedule.
    start = sample_rate//1000
    iq[start:start+len(wave)] = wave
    return iq, dict(protocol='ESB-1M-NOACK-DPL-CRC16', frequency_hz=FREQUENCY,
        channel=40, bit_rate=BIT_RATE, deviation_hz=DEVIATION,
        address_on_air_hex=ADDRESS.hex(), pid=0, no_ack=True,
        payload_hex=payload.hex(), packet_bits=len(bits),
        packet_packed_hex=np.packbits(bits, bitorder='big').tobytes().hex(),
        packed_final_padding_bits=(-len(bits))%8, interval_seconds=.01,
        external_nrf24_confirmed=False,
        limitation='Fixed cyclic packet, no ACK/retry engine; external chip interoperability unverified')
