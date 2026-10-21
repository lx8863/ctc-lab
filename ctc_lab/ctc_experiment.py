"""CTC experiment orchestration. Evaluator gets TX truth only after RX decoding."""
from __future__ import annotations
import numpy as np
from scipy.signal import butter,sosfilt
from .ble_extended import event,LO_FREQUENCY,PRIMARY_FREQUENCY,SECONDARY_FREQUENCY
from .ieee802154 import build_mac
from .msk_receiver import decode as decode_802154
from .extended_receiver import decode as decode_ble


def downconvert(iq: np.ndarray, offset_hz: int,
                sample_rate: int = 8_000_000) -> np.ndarray:
    shifted = iq*np.exp(-2j*np.pi*offset_hz*np.arange(len(iq))/sample_rate)
    # Fixed channel filter, identical for standard and CTC signal inputs.
    sos = butter(5,1_200_000,fs=sample_rate,output="sos")
    return sosfilt(sos,shifted).astype(np.complex64)


def receive(iq: np.ndarray, sample_rate: int = 8_000_000) -> dict:
    """Only RF-band configuration is known. No event schedule or payload input."""
    primary = downconvert(iq,PRIMARY_FREQUENCY-LO_FREQUENCY,sample_rate)
    auxiliary = downconvert(iq,SECONDARY_FREQUENCY-LO_FREQUENCY,sample_rate)
    return dict(ieee802154_frames=decode_802154(auxiliary,sample_rate),
                ble_primary_frames=decode_ble(primary,38,sample_rate),
                ble_auxiliary_frames=decode_ble(auxiliary,12,sample_rate))


def cycle(payloads: list[bytes], sample_rate: int = 8_000_000) -> tuple[np.ndarray,list[dict]]:
    if not 1<=len(payloads)<=3:
        raise ValueError("Use 1..3 target frames in each bounded TX cycle")
    intervals = [0.103125,0.107375,0.101875]
    parts = []
    manifests = []
    for index,payload in enumerate(payloads):
        psdu = build_mac(payload,sequence=index)
        wave,manifest = event(psdu,sample_rate,did=0x100+index)
        samples = round(intervals[index]*sample_rate)
        begin = round(20e-6*sample_rate)
        if begin+len(wave)>=samples:
            raise ValueError("Event exceeds advertising interval")
        block = np.zeros(samples,dtype=np.complex64)
        block[begin:begin+len(wave)] = wave
        parts.append(block)
        manifest["interval_seconds"] = intervals[index]
        manifests.append(manifest)
    return np.concatenate(parts),manifests


def evaluate(decoded: dict, manifests: list[dict]) -> dict:
    expected = {m["desired_ieee802154_psdu_hex"] for m in manifests}
    good = [f for f in decoded["ieee802154_frames"] if f["fcs_ok"]]
    actual = {f["psdu_hex"] for f in good}
    ble_expected = {m["auxiliary_pdu_hex"] for m in manifests}
    ble_actual = {f["pdu_hex"] for f in decoded["ble_auxiliary_frames"] if f["crc_ok"]}
    return dict(decoded,expected_unique_frames=len(expected),
        matched_unique_frames=len(expected&actual),unexpected_valid_frames=len(actual-expected),
        ieee802154_fcs_pass=len(good),
        ieee802154_fcs_fail=len(decoded["ieee802154_frames"])-len(good),
        ble_matched_unique_auxiliary_pdus=len(ble_actual&ble_expected),
        ble_unexpected_valid_pdus=len(ble_actual-ble_expected))
