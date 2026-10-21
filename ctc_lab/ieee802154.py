"""2.4 GHz IEEE 802.15.4 O-QPSK PHY and a deliberately small MAC subset.

Chip strings are C0 first, as printed in TI SWRU191F, Table 23-2.
Octets map low nibble first; even chips feed I, odd chips feed delayed Q.
This module does not implement the Zigbee network or application layers.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
import numpy as np

CHIP_RATE = 2_000_000
CHIP_WORDS = (
    0xD9C3522E, 0xED9C3522, 0x2ED9C352, 0x22ED9C35,
    0x522ED9C3, 0x3522ED9C, 0xC3522ED9, 0x9C3522ED,
    0x8C96077B, 0xB8C96077, 0x7B8C9607, 0x77B8C960,
    0x077B8C96, 0x6077B8C9, 0x96077B8C, 0xC96077B8,
)
CHIPS = np.array([[(word >> (31-k)) & 1 for k in range(32)]
                  for word in CHIP_WORDS], dtype=np.int8)
SIGNS = (2 * CHIPS - 1).astype(np.float64)
SFD = 0xA7
FCF = 0x8841  # Data, no security/ACK, PAN compression, two short addresses.


def samples_per_chip(sample_rate: int) -> int:
    if sample_rate % CHIP_RATE or sample_rate < 4_000_000:
        raise ValueError("Sample rate must be an integer multiple of 2 MHz, >=4 MHz")
    return sample_rate // CHIP_RATE


def channel_frequency(channel: int) -> int:
    if not 11 <= channel <= 26:
        raise ValueError("802.15.4 2.4 GHz channel must be 11..26")
    return 2_405_000_000 + 5_000_000 * (channel - 11)


def fcs(data: bytes) -> int:
    """CRC-16/KERMIT: reflected 0x1021, init=0, xorout=0."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc


def build_mac(payload: bytes, sequence: int = 0, pan: int = 0xCAFE,
              destination: int = 0xFFFF, source: int = 0x0001) -> bytes:
    if len(payload) > 116:
        raise ValueError("Payload exceeds 116-byte limit for this 9-byte MAC header")
    if not 0 <= sequence <= 255:
        raise ValueError("MAC sequence must be 0..255")
    for address in (pan, destination, source):
        if not 0 <= address <= 65535:
            raise ValueError("PAN and short addresses must be 16-bit integers")
    body = struct.pack("<HBHHH", FCF, sequence, pan, destination, source) + payload
    return body + struct.pack("<H", fcs(body))


def parse_mac(psdu: bytes) -> dict:
    if not 5 <= len(psdu) <= 127:
        raise ValueError("Invalid PSDU length")
    crc_ok = fcs(psdu[:-2]) == int.from_bytes(psdu[-2:], "little")
    result = dict(psdu_hex=psdu.hex(), fcs_ok=crc_ok,
                  frame_control=int.from_bytes(psdu[:2], "little"),
                  sequence=psdu[2], mac_supported=False)
    if len(psdu) >= 11 and result["frame_control"] == FCF:
        pan, destination, source = struct.unpack("<HHH", psdu[3:9])
        payload = psdu[9:-2]
        result.update(mac_supported=True, pan=pan, destination=destination,
                      source=source, payload_hex=payload.hex(),
                      payload_text=payload.decode("utf-8", errors="replace"))
    return result


def ppdu(psdu: bytes) -> bytes:
    if not 5 <= len(psdu) <= 127:
        raise ValueError("PSDU must have 5..127 bytes including FCS")
    return bytes(4) + bytes([SFD, len(psdu)]) + psdu


def octets_to_symbols(data: bytes) -> np.ndarray:
    octets = np.frombuffer(data, dtype=np.uint8)
    symbols = np.empty(2 * len(octets), dtype=np.uint8)
    symbols[0::2] = octets & 15
    symbols[1::2] = octets >> 4
    return symbols


def pulse(sample_rate: int) -> np.ndarray:
    spc = samples_per_chip(sample_rate)
    return np.sin(np.pi * np.arange(2 * spc) / (2 * spc))


def modulate_symbols(symbols: np.ndarray, sample_rate: int) -> np.ndarray:
    spc = samples_per_chip(sample_rate)
    chips = SIGNS[np.asarray(symbols, dtype=np.uint8)].reshape(-1)
    if len(chips) == 0:
        return np.empty(0, dtype=np.complex64)
    impulses = np.zeros(len(chips) * spc, dtype=np.complex128)
    impulses[0::2*spc] = chips[0::2]
    impulses[spc::2*spc] = 1j * chips[1::2]
    return np.convolve(impulses, pulse(sample_rate)).astype(np.complex64)


def modulate(psdu: bytes, sample_rate: int = 8_000_000) -> np.ndarray:
    return modulate_symbols(octets_to_symbols(ppdu(psdu)), sample_rate)


@dataclass(frozen=True)
class Burst:
    iq: np.ndarray
    frames: tuple[bytes, ...]


def build_burst(payloads: list[bytes], sample_rate: int = 8_000_000,
                gap_seconds: float = 0.002) -> Burst:
    samples_per_chip(sample_rate)
    if not payloads or gap_seconds < 0:
        raise ValueError("Need >=1 payload and nonnegative gap")
    # A guard at both ends also allows arbitrary receive-buffer alignment.
    silence = np.zeros(round(gap_seconds * sample_rate), dtype=np.complex64)
    frames = tuple(build_mac(p, i % 256) for i, p in enumerate(payloads))
    parts = [silence]
    for frame in frames:
        parts.extend((modulate(frame, sample_rate), silence))
    return Burst(np.concatenate(parts), frames)
