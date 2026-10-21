"""Blind LE 1M extended PDU demodulation for RF / file checks, no TX metadata."""
from __future__ import annotations
import numpy as np
from scipy.signal import correlate,find_peaks
from .ble import ACCESS_ADDRESS,bits_lsb,octets_lsb,whiten,crc
from .ble_extended import parse_pdu


def decode(iq: np.ndarray, channel: int, sample_rate: int = 8_000_000) -> list[dict]:
    if sample_rate%1_000_000 or sample_rate<4_000_000:
        raise ValueError("Expected >=4 integer samples per bit")
    spb = sample_rate//1_000_000
    x = np.asarray(iq,dtype=np.complex128)
    if len(x)<80*spb:
        return []
    phase = np.angle(x[1:]*np.conj(x[:-1]))
    template = 2*bits_lsb(ACCESS_ADDRESS).astype(np.int16)-1
    frames = []
    for offset in range(spb):
        count = (len(phase)-offset)//spb
        soft = phase[offset:offset+count*spb].reshape(-1,spb).mean(axis=1)
        bits = (soft>0).astype(np.uint8)
        response = correlate(2*bits.astype(float)-1,template,mode="valid",method="fft")
        peaks,_ = find_peaks(response,height=27.9,distance=32)
        for peak in peaks:
            begin = int(peak)+32
            if begin+16>len(bits):
                continue
            header = whiten(octets_lsb(bits[begin:begin+16]),channel)
            if header[0]&15!=7:
                continue
            size = (2+int(header[1])+3)*8
            if begin+size>len(bits):
                continue
            raw = whiten(octets_lsb(bits[begin:begin+size]),channel)
            if crc(raw[:-3])!=raw[-3:]:
                continue
            sample = offset+int(peak)*spb
            if any(abs(f["access_address_sample"]-sample)<2*spb for f in frames):
                continue
            try:
                parsed = parse_pdu(raw[:-3])
            except ValueError:
                continue
            parsed.update(access_address_sample=sample,channel=channel,crc_ok=True,
                          pdu_hex=raw[:-3].hex())
            frames.append(parsed)
    return sorted(frames,key=lambda frame:frame["access_address_sample"])
