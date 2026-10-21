"""Independent LE 1M batch receiver for local TX diagnostics.

Bit timing searches every sample phase. Uses only the public advertising access
address, channel index and sample rate, never the transmitted payload or start.
Phone reception remains the external interoperability acceptance test.
"""
from __future__ import annotations
import numpy as np
from scipy.signal import correlate,find_peaks
from .ble import ACCESS_ADDRESS,bits_lsb,octets_lsb,whiten,crc


def decode_advertisements(iq: np.ndarray, channel: int = 37,
                          sample_rate: int = 8_000_000) -> list[dict]:
    if sample_rate%1_000_000 or sample_rate<4_000_000:
        raise ValueError("Expected >=4 integer samples per LE 1M bit")
    data = np.asarray(iq,dtype=np.complex128)
    if data.ndim!=1 or not np.isfinite(data).all():
        raise ValueError("Expected finite 1D IQ")
    if len(data)<sample_rate//1000:
        return []
    spb = sample_rate//1_000_000
    discriminator = np.angle(data[1:]*np.conj(data[:-1]))
    template = 2*bits_lsb(ACCESS_ADDRESS).astype(np.int16)-1
    frames = []
    for phase in range(spb):
        usable = (len(discriminator)-phase)//spb*spb
        soft = discriminator[phase:phase+usable].reshape(-1,spb).mean(axis=1)
        bits = (soft>0).astype(np.uint8)
        match = correlate(2*bits.astype(np.float64)-1,template,mode="valid",method="fft")
        peaks,_ = find_peaks(match,height=27.9,distance=32)
        for start in peaks:
            first = int(start)+32
            if first+16>len(bits):
                continue
            header = whiten(octets_lsb(bits[first:first+16]),channel)
            length = header[1]&0x3F
            if header[0]&15 != 2 or header[0]&0x20 or header[1]&0xC0 or not 6<=length<=37:
                continue
            count = (2+length+3)*8
            if first+count>len(bits):
                continue
            decoded = whiten(octets_lsb(bits[first:first+count]),channel)
            if crc(decoded[:-3]) != decoded[-3:]:
                continue
            sample = phase+int(start)*spb
            if any(abs(frame["access_address_sample"]-sample)<2*spb for frame in frames):
                continue
            adv_data = decoded[8:-3]
            fields = []
            cursor = 0
            while cursor<len(adv_data) and adv_data[cursor]:
                size = adv_data[cursor]
                if cursor+size+1>len(adv_data):
                    break
                fields.append(dict(type=int(adv_data[cursor+1]),
                                   data_hex=adv_data[cursor+2:cursor+size+1].hex()))
                cursor += size+1
            frame = dict(access_address_sample=sample,crc_ok=True,pdu_hex=decoded[:-3].hex(),
                advertising_address=":".join(f"{v:02X}" for v in decoded[2:8][::-1]),
                advertising_data_hex=adv_data.hex(),ad_fields=fields)
            for field in fields:
                raw = bytes.fromhex(field["data_hex"])
                if field["type"]==9:
                    frame["name"] = raw.decode("utf-8",errors="replace")
                if field["type"]==255 and len(raw)>=2:
                    frame["manufacturer_id"] = int.from_bytes(raw[:2],"little")
                    frame["test_marker_hex"] = raw[2:].hex()
            frames.append(frame)
    return sorted(frames,key=lambda frame:frame["access_address_sample"])
