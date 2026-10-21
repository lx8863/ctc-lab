"""LE 1M non-connectable legacy advertising for controlled lab verification.

CRC / whitening / wire order verified against Bluetooth Core Vol 6 Part C 4.2.1.
No connections, scan responses, or Bluetooth network stack are implemented.
"""
from __future__ import annotations
import numpy as np

ACCESS_ADDRESS = bytes.fromhex("d6be898e")
DEFAULT_ADDRESS = "C2:54:43:00:00:01"
DEFAULT_NAME = "SDR-BLE-TEST"


def bits_lsb(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data,dtype=np.uint8),bitorder="little")


def octets_lsb(bits: np.ndarray) -> bytes:
    bits = np.asarray(bits,dtype=np.uint8)
    if len(bits)%8:
        raise ValueError("BLE octet sequence requires a multiple of 8 bits")
    return np.packbits(bits,bitorder="little").tobytes()


def frequency(channel: int) -> int:
    if channel not in (37,38,39):
        raise ValueError("Primary advertising channel must be 37, 38, or 39")
    return {37:2_402_000_000,38:2_426_000_000,39:2_480_000_000}[channel]


def crc(pdu: bytes) -> bytes:
    # State numbering follows the normative MSB-output polynomial diagram.
    state = 0x555555
    for bit in bits_lsb(pdu):
        feedback = ((state>>23)^int(bit))&1
        state = (state<<1)&0xFFFFFF
        if feedback:
            state ^= 0x00065B
    # CRC is transmitted state bit 23 first, unlike ordinary PDU octets.
    return octets_lsb(np.array([(state>>k)&1 for k in range(23,-1,-1)]))


def whiten(data: bytes, channel: int) -> bytes:
    if not 0 <= channel <= 39:
        raise ValueError("BLE physical channel index must be 0..39")
    state = channel|0x40
    bits = bits_lsb(data).copy()
    for i in range(len(bits)):
        output = state&1
        bits[i] ^= output
        state >>= 1
        if output:
            state ^= 0x44
    return octets_lsb(bits)


def advertising_pdu(adv_data: bytes, address: str = DEFAULT_ADDRESS) -> bytes:
    if len(adv_data)>31:
        raise ValueError("Legacy AdvData cannot exceed 31 bytes")
    try:
        parsed = bytes.fromhex(address.replace(":",""))
    except ValueError as exc:
        raise ValueError("Invalid six-byte advertising address") from exc
    if len(parsed)!=6 or parsed[0]&0xC0 != 0xC0:
        raise ValueError("Expected six-byte static random address with top bits 11")
    random_part = int.from_bytes(parsed,"big") & ((1<<46)-1)
    if random_part in (0,(1<<46)-1):
        raise ValueError("Static random address has a reserved all-zero/all-one random part")
    body = parsed[::-1]+adv_data
    return bytes([0x42,len(body)])+body


def advertising_data(name: str = DEFAULT_NAME,
                     marker: bytes = bytes.fromhex("12345678")) -> bytes:
    encoded = name.encode("utf-8")
    # 0xffff is an experimental marker only, not an assigned company identity.
    manufacturer = bytes.fromhex("ffff")+marker
    if not encoded or len(encoded)>27 or len(manufacturer)>253:
        raise ValueError("Advertising name or marker is too long")
    result = (bytes.fromhex("020106") + bytes([len(encoded)+1,0x09])+encoded
              +bytes([len(manufacturer)+1,0xFF])+manufacturer)
    if len(result)>31:
        raise ValueError("Name and marker do not fit in 31-byte legacy AdvData")
    return result


def wire_packet(pdu: bytes, channel: int) -> bytes:
    frequency(channel)
    return b"\xaa"+ACCESS_ADDRESS+whiten(pdu+crc(pdu),channel)


def gfsk(packet: bytes, sample_rate: int = 8_000_000) -> np.ndarray:
    if sample_rate%1_000_000 or sample_rate<4_000_000:
        raise ValueError("LE 1M needs integer samples per bit and >=4 MSPS")
    if not packet:
        raise ValueError("Empty BLE packet")
    spb = sample_rate//1_000_000
    nrz = np.repeat(bits_lsb(packet).astype(np.float64)*2-1,spb)
    # Gaussian impulse response: -3 dB bandwidth B, BT=0.5, h=0.5.
    t = np.arange(-2*spb,2*spb+1)/spb
    kernel = np.exp(-2*np.pi**2*0.5**2*t*t/np.log(2))
    kernel /= kernel.sum()
    padded = np.pad(nrz,(2*spb,2*spb),mode="edge")
    frequency_pulse = np.convolve(padded,kernel,mode="valid")
    phase = np.cumsum(frequency_pulse)*(2*np.pi*250_000/sample_rate)
    return np.exp(1j*phase).astype(np.complex64)


def advertising_cycle(pdu: bytes, channel: int = 37,
                      sample_rate: int = 8_000_000) -> tuple[np.ndarray,dict]:
    packet = wire_packet(pdu,channel)
    wave = gfsk(packet,sample_rate)
    # Three advertising events with bounded pseudo-random 0..10 ms advDelay.
    # A cyclic replay repeats this finite schedule; not a full GAP controller.
    intervals = [0.103125,0.107375,0.101875]
    events = []
    for interval in intervals:
        event = np.zeros(round(interval*sample_rate),dtype=np.complex64)
        start = round(20e-6*sample_rate)
        event[start:start+len(wave)] = wave
        events.append(event)
    cycle = np.concatenate(events)
    return cycle,dict(channel=channel,frequency_hz=frequency(channel),
        access_address="8E89BED6",pdu_hex=pdu.hex(),crc_hex=crc(pdu).hex(),
        packet_on_air_octets_hex=packet.hex(),pdu_type="ADV_NONCONN_IND",
        interval_seconds=intervals,packet_duration_us=len(packet)*8,
        cycle_seconds=len(cycle)/sample_rate,
        limitation="One enabled advertising channel; finite cyclic replay, no GAP controller")
