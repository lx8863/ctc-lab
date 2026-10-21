"""ESB receiver. Only public address/rate and IQ, never TX length or payload."""
from __future__ import annotations
import numpy as np
from scipy.signal import correlate, find_peaks
from .esb import ADDRESS, BIT_RATE, bits_msb, crc16


def _integer(bits):
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def decode(iq: np.ndarray, sample_rate: int = 8_000_000,
           address: bytes = ADDRESS) -> list[dict]:
    if sample_rate % BIT_RATE or sample_rate < 4_000_000 or len(address) != 5:
        raise ValueError('ESB requires a 5-byte address and >=4 integer samples/bit')
    iq = np.asarray(iq, dtype=np.complex128)
    if iq.ndim != 1 or not np.isfinite(iq).all():
        raise ValueError('Expected finite 1D complex IQ')
    spb = sample_rate//BIT_RATE
    if len(iq) < 80*spb:
        return []
    discriminator = np.angle(iq[1:]*np.conj(iq[:-1]))
    address_bits = bits_msb(address)
    template = address_bits.astype(float)*2-1
    preamble = bits_msb(bytes([0xAA if address[0] & 0x80 else 0x55]))
    frames = []
    for phase in range(spb):
        usable = (len(discriminator)-phase)//spb*spb
        soft = discriminator[phase:phase+usable].reshape(-1,spb).mean(axis=1)
        if len(soft) < 80:
            continue
        # Address correlation acquires bit timing without consulting the payload.
        hard = (soft > 0).astype(np.uint8)
        match = correlate(hard.astype(float)*2-1, template, mode='valid', method='fft')
        peaks, _ = find_peaks(match, height=35.9, distance=40)
        for start in peaks:
            start = int(start)
            if start < 8 or start+49 > len(soft):
                continue
            training = soft[start:start+40]
            bias = (training[address_bits==0].mean()+training[address_bits==1].mean())/2
            header = (soft[start-8:start+49] > bias).astype(np.uint8)
            if not np.array_equal(header[:8], preamble) or not np.array_equal(header[8:48], address_bits):
                continue
            length = _integer(header[48:54])
            if not 1 <= length <= 32:
                continue
            count = 40+9+length*8+16
            if start+count > len(soft):
                continue
            bits = (soft[start:start+count] > bias).astype(np.uint8)
            if crc16(bits[:-16]) != _integer(bits[-16:]):
                continue
            sample = phase+start*spb
            if any(abs(frame['address_sample']-sample) < 2*spb for frame in frames):
                continue
            payload = np.packbits(bits[49:-16], bitorder='big').tobytes()
            frames.append(dict(address_sample=sample, address_on_air_hex=address.hex(),
                payload_length=length, pid=_integer(bits[46:48]), no_ack=bool(bits[48]),
                crc_ok=True, crc_hex=f'{_integer(bits[-16:]):04x}', payload_hex=payload.hex(),
                payload_text=payload.decode('utf-8',errors='replace'),
                cfo_hz=float(bias*sample_rate/(2*np.pi))))
    return sorted(frames,key=lambda frame:frame['address_sample'])
