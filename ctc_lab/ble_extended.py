"""Non-connectable extended advertising event for long, constrained CTC data.

Primary ADV_EXT_IND: channel 38 / 2426 MHz, ADI + AuxPtr.
Secondary AUX_ADV_IND: channel 12 / 2430 MHz, AdvA + same ADI + AdvData.
Both LE 1M. AuxPtr points 600 us from primary start; actual start is 615 us.
One buffer at LO 2428 MHz carries the packets sequentially at -2 / +2 MHz.
This is an explicit event waveform, not a complete GAP controller/certified stack.
"""
from __future__ import annotations
import numpy as np
from .ble import ACCESS_ADDRESS,crc,whiten,gfsk,advertising_pdu
from .ctc_mapper import encode_frame,capacity_report

PRIMARY_CHANNEL = 38
SECONDARY_CHANNEL = 12
PRIMARY_FREQUENCY = 2_426_000_000
SECONDARY_FREQUENCY = 2_430_000_000
LO_FREQUENCY = 2_428_000_000
CTC_ADDRESS = "C2:54:43:00:00:02"
CTC_NAME = "SDR-CTC-TEST"


def packet(pdu: bytes, channel: int) -> bytes:
    if not 0<=channel<=39:
        raise ValueError("Invalid BLE channel")
    return b"\xaa"+ACCESS_ADDRESS+whiten(pdu+crc(pdu),channel)


def _adi(did: int, sid: int = 2) -> bytes:
    if not 0<=did<4096 or not 0<=sid<16:
        raise ValueError("Invalid advertising set / data ID")
    return ((sid<<12)|did).to_bytes(2,"little")


def primary_pdu(did: int = 1) -> bytes:
    # Flags: ADI (bit 3), AuxPtr (bit 4); no AdvA in this primary PDU.
    # AuxPtr: channel=12, CA=0 (51..500ppm), units=30us, offset=20, PHY=LE1M.
    aux_ptr = (SECONDARY_CHANNEL | (20<<8)).to_bytes(3,"little")
    body = bytes([6,0x18])+_adi(did)+aux_ptr
    return bytes([0x07,len(body)])+body


def _aux_prefix(mapped_size: int, did: int) -> bytes:
    # Reuse validated address validation, not its legacy PDU length restriction.
    address = advertising_pdu(b"",CTC_ADDRESS)[2:8]
    ext_header = bytes([9,0x09])+address+_adi(did)
    name = CTC_NAME.encode("ascii")
    ad_prefix = bytes.fromhex("020106")+bytes([len(name)+1,9])+name
    # Two guard bytes keep the final target symbol away from BLE CRC transition.
    manufacturer_length = 1+2+mapped_size+2
    if manufacturer_length>255:
        raise ValueError("CTC region too large for an AD structure")
    return ext_header+ad_prefix+bytes([manufacturer_length,0xFF,0xFF,0xFF])


def auxiliary_pdu(psdu: bytes, did: int = 1) -> tuple[bytes,dict]:
    desired = encode_frame(psdu)+b"\x55\x55"
    prefix = _aux_prefix(len(desired)-2,did)
    length = len(prefix)+len(desired)
    if length>255:
        raise ValueError("CTC frame exceeds unchained AUX_ADV_IND payload capacity")
    header = bytes([0x47,length])
    offset = len(header)+len(prefix)
    whitening_mask = whiten(bytes(offset+len(desired)),SECONDARY_CHANNEL)
    raw_region = bytes(a^b for a,b in zip(desired,whitening_mask[offset:]))
    pdu = header+prefix+raw_region
    on_air = packet(pdu,SECONDARY_CHANNEL)
    assert on_air[5+offset:5+offset+len(desired)] == desired
    return pdu,dict(capacity=capacity_report(psdu),
        did=did,source_pdu_type="AUX_ADV_IND",source_channel=SECONDARY_CHANNEL,
        source_pdu_payload_bytes=length,ctc_on_air_byte_offset=5+offset,
        ctc_on_air_bytes=len(desired)-2,guard_bytes=2,
        advertising_address=CTC_ADDRESS,name=CTC_NAME,
        manufacturer_data_hex=(b"\xff\xff"+raw_region).hex(),
        desired_ieee802154_psdu_hex=psdu.hex())


def event(psdu: bytes, sample_rate: int = 8_000_000,
          did: int = 1) -> tuple[np.ndarray,dict]:
    if sample_rate<8_000_000:
        raise ValueError("Two-frequency extended event needs >=8 MSPS")
    main = primary_pdu(did)
    aux,metadata = auxiliary_pdu(psdu,did)
    primary_wave = gfsk(packet(main,PRIMARY_CHANNEL),sample_rate)
    secondary_wave = gfsk(packet(aux,SECONDARY_CHANNEL),sample_rate)
    aux_start = round(615e-6*sample_rate)
    primary_end_us = len(primary_wave)/sample_rate*1e6
    if 600<primary_end_us+300:
        raise ValueError("AuxPtr violates minimum AUX frame space")
    length = aux_start+len(secondary_wave)+round(100e-6*sample_rate)
    iq = np.zeros(length,dtype=np.complex64)
    for wave,begin,frequency in ((primary_wave,0,PRIMARY_FREQUENCY),
                                 (secondary_wave,aux_start,SECONDARY_FREQUENCY)):
        n = np.arange(len(wave))
        iq[begin:begin+len(wave)] = wave*np.exp(
            2j*np.pi*(frequency-LO_FREQUENCY)*n/sample_rate)
    metadata.update(primary_channel=PRIMARY_CHANNEL,primary_frequency_hz=PRIMARY_FREQUENCY,
        secondary_frequency_hz=SECONDARY_FREQUENCY,lo_frequency_hz=LO_FREQUENCY,
        ieee802154_channel=16,primary_pdu_hex=main.hex(),auxiliary_pdu_hex=aux.hex(),
        primary_duration_us=primary_end_us,aux_ptr_offset_us=600,
        auxiliary_start_us=615,minimum_aux_frame_space_us=300,
        actual_aux_frame_space_us=615-primary_end_us,
        ctc_start_sample=aux_start+(metadata["ctc_on_air_byte_offset"]*8*sample_rate//1_000_000),
        primary_duration_samples=len(primary_wave),auxiliary_start_sample=aux_start,
        auxiliary_duration_samples=len(secondary_wave))
    return iq,metadata


def parse_pdu(pdu: bytes) -> dict:
    """Independent structure parser; no mapping, desired data, or LUT required."""
    if len(pdu)<4 or pdu[0]&15!=7 or len(pdu)!=pdu[1]+2:
        raise ValueError("Not a length-consistent extended advertising PDU")
    ext_len = pdu[2]&0x3F
    mode = pdu[2]>>6
    end = 3+ext_len
    if mode!=0 or ext_len<1 or end>len(pdu):
        raise ValueError("Unsupported extended header")
    flags = pdu[3]
    cursor = 4
    result = dict(adv_mode=mode,flags=flags)
    fields = ((1,6,"address"),(2,6,"target_address"),(4,1,"cte_info"),
              (8,2,"adi"),(16,3,"aux_ptr"),(32,18,"sync_info"),(64,1,"tx_power"))
    for flag,size,name in fields:
        if flags&flag:
            if cursor+size>end:
                raise ValueError("Extended header field exceeds header length")
            raw = pdu[cursor:cursor+size]
            cursor += size
            result[name+"_hex"] = raw.hex()
            if name=="address":
                result["address"] = ":".join(f"{b:02X}" for b in raw[::-1])
            elif name=="aux_ptr":
                word = int.from_bytes(raw,"little")
                unit = 300 if word&128 else 30
                result["aux_channel"] = word&63
                result["aux_offset_us"] = ((word>>8)&8191)*unit
                result["aux_phy"] = word>>21
    if cursor!=end:
        raise ValueError("Unexpected ACAD / header padding in this laboratory subset")
    adv_data = pdu[end:]
    result["advertising_data_hex"] = adv_data.hex()
    cursor = 0
    while cursor<len(adv_data):
        size = adv_data[cursor]
        if size==0:
            break
        if cursor+size+1>len(adv_data):
            raise ValueError("Malformed AD structure")
        kind = adv_data[cursor+1]
        raw = adv_data[cursor+2:cursor+size+1]
        if kind==9:
            result["name"] = raw.decode("utf-8",errors="replace")
        elif kind==255:
            result["manufacturer_data_hex"] = raw.hex()
        cursor += size+1
    return result
