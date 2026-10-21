"""Blind, batch 802.15.4 receiver: IQ and sample rate are the only inputs.

Acquisition uses known public preamble sequences, a CFO bank, and normalized
correlation. Carrier refinement is fitted to the preamble. Half-sine matched
filters recover I/Q chips and standard 16-way DSSS despreading recovers symbols.
No transmitted payload, packet length, frame count, or start indices are inputs.
"""
from __future__ import annotations

from functools import lru_cache
import numpy as np
from scipy.signal import correlate, find_peaks

from .ieee802154 import (SIGNS, SFD, modulate_symbols, parse_mac, pulse,
                        samples_per_chip)


@lru_cache(maxsize=8)
def _reference(sample_rate: int) -> np.ndarray:
    length = 32 * samples_per_chip(sample_rate)
    wave = modulate_symbols(np.zeros(6, dtype=np.uint8), sample_rate)
    # Interior symbol includes the Q pulse from its predecessor.
    return wave[length:5*length].astype(np.complex128)


def _octet(symbols: np.ndarray, index: int) -> int:
    return int(symbols[index]) | (int(symbols[index+1]) << 4)


def _decode_candidate(iq: np.ndarray, start: int, coarse_cfo: float,
                      sample_rate: int, threshold: float) -> dict | None:
    spc = samples_per_chip(sample_rate)
    reference = _reference(sample_rate)
    available = iq[start:start+len(reference)]
    if len(available) != len(reference):
        return None
    n = np.arange(len(reference))
    corrected = available * np.exp(-2j*np.pi*coarse_cfo*n/sample_rate)
    product = corrected * np.conj(reference)
    # Coarse CFO is within 5 kHz. Adjacent residual phases are unambiguous.
    phase = np.unwrap(np.angle(product))
    slope, intercept = np.polyfit(n, phase, 1)
    cfo = coarse_cfo + slope * sample_rate / (2*np.pi)
    fitted = corrected * np.exp(-1j*(slope*n+intercept))
    quality = abs(np.vdot(reference, fitted)) / np.sqrt(
        np.vdot(reference, reference).real * np.vdot(fitted, fitted).real + 1e-30)
    if quality < threshold:
        return None
    # Bound capture work to the longest allowed frame plus acquisition slack.
    segment = iq[start:start+(4+1+1+127+4)*64*spc+2*spc]
    n = np.arange(len(segment))
    baseband = segment * np.exp(-1j*(2*np.pi*cfo*n/sample_rate+intercept))
    h = pulse(sample_rate)
    i_matched = correlate(baseband.real, h, mode="valid", method="auto")
    q_matched = correlate(baseband.imag, h, mode="valid", method="auto")
    count = (len(i_matched)-spc) // (32*spc)
    if count < 6:
        return None
    chip_starts = np.arange(count*32)*spc
    chips = np.empty(count*32, dtype=np.float64)
    chips[0::2] = i_matched[chip_starts[0::2]]
    chips[1::2] = q_matched[chip_starts[1::2]]
    scores = chips.reshape(-1,32) @ SIGNS.T
    symbols = np.argmax(scores, axis=1)
    for sfd in range(1, min(10, len(symbols)-3)):
        if symbols[sfd] != (SFD & 15) or symbols[sfd+1] != (SFD >> 4):
            continue
        if not np.all(symbols[:sfd] == 0):
            continue
        length = _octet(symbols, sfd+2)
        if not 5 <= length <= 127:
            continue
        begin = sfd+4
        if len(symbols) < begin+2*length:
            continue
        psdu = bytes(_octet(symbols, begin+2*k) for k in range(length))
        result = parse_mac(psdu)
        end_sample = start + (begin+2*length)*32*spc + spc
        winning = np.max(scores[:begin+2*length], axis=1)
        denominator = np.sum(np.abs(chips.reshape(-1,32)[:begin+2*length]),axis=1)
        result.update(start_sample=max(0,start+(sfd-8)*32*spc),
                      sfd_sample=start+sfd*32*spc, end_sample=end_sample,
                      psdu_length=length, cfo_hz=float(cfo),
                      preamble_correlation=float(quality),
                      mean_symbol_correlation=float(np.mean(winning/(denominator+1e-30))))
        return result
    return None


def decode(iq: np.ndarray, sample_rate: int = 8_000_000,
           max_cfo_hz: float = 100_000, threshold: float = 0.72,
           max_candidates: int = 512) -> list[dict]:
    """Return detected frames, including FCS failures; caller decides acceptance.

    max_cfo_hz is an acquisition search limit, not a promised operating range.
    No adaptive sample-clock recovery or multipath equalizer in this first version.
    """
    spc = samples_per_chip(sample_rate)
    if not 0 < threshold < 1 or max_cfo_hz < 0 or max_candidates < 1:
        raise ValueError("Invalid receiver search configuration")
    iq = np.asarray(iq, dtype=np.complex128)
    if iq.ndim != 1 or not np.isfinite(iq).all():
        raise ValueError("IQ must be a finite one-dimensional complex array")
    ref = _reference(sample_rate)
    if len(iq) < len(ref):
        return []
    power = np.abs(iq)**2
    cumulative = np.concatenate(([0.], np.cumsum(power)))
    energy = cumulative[len(ref):] - cumulative[:-len(ref)]
    denominator = np.sqrt(np.maximum(energy,0)*np.vdot(ref,ref).real+1e-30)
    candidates = []
    bank = np.arange(-np.ceil(max_cfo_hz/10_000),
                     np.ceil(max_cfo_hz/10_000)+1)*10_000
    for frequency in bank:
        shifted = ref * np.exp(2j*np.pi*frequency*np.arange(len(ref))/sample_rate)
        response = np.abs(correlate(iq, shifted, mode="valid", method="fft"))/denominator
        peaks, _ = find_peaks(response, height=threshold, distance=16*spc)
        candidates.extend((float(response[k]),int(k),float(frequency)) for k in peaks)
    candidates.sort(reverse=True)
    frames = []
    seen = []
    for _, start, frequency in candidates[:max_candidates]:
        if any(a <= start < b for a,b in seen):
            continue
        decoded = _decode_candidate(iq,start,frequency,sample_rate,threshold)
        if decoded is None:
            continue
        # Duplicate detection by time, not sequence number: wraparound is legal.
        existing = next((f for f in frames if abs(f["sfd_sample"]-decoded["sfd_sample"])<4*spc),None)
        if existing is None:
            frames.append(decoded)
        elif decoded["fcs_ok"] and not existing["fcs_ok"]:
            frames[frames.index(existing)] = decoded
        if decoded["fcs_ok"]:
            seen.append((max(0,decoded["start_sample"]-spc),decoded["end_sample"]))
    return sorted(frames,key=lambda f:f["sfd_sample"])
