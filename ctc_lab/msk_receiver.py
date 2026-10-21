"""Noncoherent half-sine O-QPSK/MSK receiver with a fixed standard dictionary.

At successive I/Q chip peaks a half-sine O-QPSK waveform has differential
phase sign (-1)**(n+1) * chip[n-1] * chip[n]. The first chip of each symbol
depends on its predecessor and is erased; the other 31 define fixed codewords.
No BLE patterns, target payloads, or transmitter lookup tables are imported.
The original coherent receiver is deliberately retained as a separate baseline.
"""
from __future__ import annotations
import numpy as np
from scipy.signal import correlate,find_peaks
from .ieee802154 import SIGNS,samples_per_chip,parse_mac

DIFFERENTIAL = (SIGNS[:,1:]*SIGNS[:,:-1]
                *(-1.)**(np.arange(1,32)+1)).astype(np.int16)


def decide(phase_increments: np.ndarray, bias: float = 0.) -> tuple[np.ndarray,np.ndarray]:
    """31 internal phase increments per symbol; hard DSSS decisions, no ML model."""
    values = np.asarray(phase_increments)
    if values.ndim!=2 or values.shape[1]!=31:
        raise ValueError("Expected N x 31 internal phase increments")
    chips = np.where(values>bias,1,-1).astype(np.int16)
    scores = chips @ DIFFERENTIAL.T
    return np.argmax(scores,axis=1),scores


def aligned_symbols(iq: np.ndarray, sample_rate: int, starts: np.ndarray,
                    bias: float = 0.) -> tuple[np.ndarray,np.ndarray]:
    """Symbol-level bench only: known boundaries are allowed for LUT QA.

    Packet reception uses decode() below, which searches unknown boundaries.
    """
    spc = samples_per_chip(sample_rate)
    starts = np.asarray(starts,dtype=int)
    positions = starts[:,None]+np.arange(1,32)[None,:]*spc
    if np.any(positions<0) or np.any(positions+spc>=len(iq)):
        raise ValueError("Symbol extends outside capture")
    phases = np.angle(iq[positions+spc]*np.conj(iq[positions]))
    return decide(phases,bias)


def _candidate(iq,phase_stream,start,spc,sample_rate,correlation):
    # First four repeated symbols estimate common phase slope without knowing
    # whether input is O-QPSK or any other modulator.
    positions = start+np.arange(4)[:,None]*32*spc+np.arange(1,32)[None,:]*spc
    if positions.max()>=len(phase_stream):
        return None
    training = phase_stream[positions].reshape(-1)
    low,high = np.percentile(training,[10,90])
    bias = float((low+high)/2)
    count = min((len(phase_stream)-start)//(32*spc),280)
    if count<12:
        return None
    positions = start+np.arange(count)[:,None]*32*spc+np.arange(1,32)[None,:]*spc
    symbols,scores = decide(phase_stream[positions],bias)
    for sfd in range(2,min(11,len(symbols)-4)):
        if symbols[sfd]!=7 or symbols[sfd+1]!=10 or not np.all(symbols[:sfd]==0):
            continue
        length = int(symbols[sfd+2]) | (int(symbols[sfd+3])<<4)
        if not 5<=length<=127:
            continue
        begin = sfd+4
        if begin+2*length>len(symbols):
            continue
        used = scores[:begin+2*length]
        # 11 error ceiling is a lab receiver parameter, not a chipset claim.
        errors = (31-np.max(used,axis=1))//2
        if np.max(errors)>11:
            continue
        psdu = bytes(int(symbols[begin+2*k])|(int(symbols[begin+2*k+1])<<4)
                     for k in range(length))
        result = parse_mac(psdu)
        second = np.partition(used,-2,axis=1)[:,-2]
        result.update(receiver="fixed-standard-differential-DSSS-31",
            sfd_sample=int(start+sfd*32*spc),
            start_sample=int(max(0,start+(sfd-8)*32*spc)),
            end_sample=int(start+(begin+2*length)*32*spc+spc),
            phase_bias_hz=bias*sample_rate/(2*np.pi*spc),
            preamble_correlation=float(correlation),
            maximum_internal_chip_errors=int(np.max(errors)),
            minimum_symbol_margin=int(np.min(np.max(used,axis=1)-second)))
        return result
    return None


def decode(iq: np.ndarray, sample_rate: int = 8_000_000,
           threshold: float = 0.37, max_candidates: int = 512) -> list[dict]:
    """Blind full-frame receiver. Uses only IQ, sample rate, public preamble.

    Threshold is phase-pattern correlation, different from coherent RF template
    correlation. Phase wrapping limits usable CFO; no continuous clock tracking.
    """
    spc = samples_per_chip(sample_rate)
    iq = np.asarray(iq,dtype=np.complex128)
    if iq.ndim!=1 or not np.isfinite(iq).all():
        raise ValueError("Expected finite 1D complex IQ")
    if not 0<threshold<1 or max_candidates<1:
        raise ValueError("Invalid acquisition configuration")
    if len(iq)<160*spc:
        return []
    stream = np.angle(iq[spc:]*np.conj(iq[:-spc]))
    one = np.r_[0,DIFFERENTIAL[0].astype(float)-DIFFERENTIAL[0].mean()]
    ref = np.tile(one,4)
    mask = np.tile(np.r_[0,np.ones(31)],4)
    ref_energy = np.sum(ref**2)
    candidates = []
    for offset in range(spc):
        chips = stream[offset::spc]
        sums = correlate(chips,mask,mode="valid",method="fft")
        squares = correlate(chips**2,mask,mode="valid",method="fft")
        variance = np.maximum(squares-sums*sums/mask.sum(),0)
        response = correlate(chips,ref,mode="valid",method="fft") / np.sqrt(
            variance*ref_energy+1e-25)
        peaks,_ = find_peaks(response,height=threshold,distance=8)
        candidates.extend((float(response[k]),int(k)*spc+offset) for k in peaks)
    candidates.sort(reverse=True)
    frames = []
    successful_spans = []
    for quality,start in candidates[:max_candidates]:
        if any(a<=start<b for a,b in successful_spans):
            continue
        # Refine sub-chip / adjacent-chip timing using the same fixed dictionary.
        # This is payload-independent: candidates rank by CRC then DSSS margin.
        variants = []
        for displacement in range(-2*spc,2*spc+1):
            refined_start = start+displacement
            if refined_start<0:
                continue
            trial = _candidate(iq,stream,refined_start,spc,sample_rate,quality)
            if trial is not None:
                variants.append(trial)
        if not variants:
            continue
        result = max(variants,key=lambda item:(item["fcs_ok"],item["minimum_symbol_margin"],
                                              -item["maximum_internal_chip_errors"]))
        previous = next((f for f in frames if abs(f["sfd_sample"]-result["sfd_sample"])<4*spc),None)
        if previous is None:
            frames.append(result)
        elif result["fcs_ok"] and not previous["fcs_ok"]:
            frames[frames.index(previous)] = result
        if result["fcs_ok"]:
            successful_spans.append((result["start_sample"]-spc,result["end_sample"]))
    return sorted(frames,key=lambda frame:frame["sfd_sample"])
