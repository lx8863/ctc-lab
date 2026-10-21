"""Reference-paired ORS feedback extension. Not the paper's quadrant decoder.

Raw half-sine OQPSK +/- ORS carries 2 status bits, 8 sequence bits and CRC8.
All decoding uses IQ and public frame structure, never TX truth or schedule.
"""
from __future__ import annotations
import numpy as np
from .ors import ack_wave
from .ors_receiver import _shape_score

STATUS = {0:'接收成功',1:'接收失败',2:'重传接收成功',3:'重传接收失败'}
FS = 8_000_000
SLOT = 160  # 16 us ORS + 4 us guard, at 8 MSPS
SLOTS = 39  # 3-slot sync + 18 reference/data pairs
FRAME_SAMPLES = SLOT*SLOTS


def crc8(data):
    state = 0
    for byte in data:
        state ^= byte
        for _ in range(8):
            state = ((state<<1)^0x07)&255 if state&128 else (state<<1)&255
    return state


def feedback_wave(status,sequence):
    if status not in STATUS or not 0<=sequence<=255:
        raise ValueError('ACK status must be 0..3 and sequence 0..255')
    check = crc8(bytes([0xA7,status,sequence]))
    value = (status<<16)|(sequence<<8)|check
    bits = [(value>>k)&1 for k in range(17,-1,-1)]
    signs = [1,-1,1]
    for bit in bits:
        signs.extend((1,1 if bit==0 else -1))
    wave = ack_wave(pair=(1,1))
    out = np.zeros(FRAME_SAMPLES,dtype=np.complex64)
    for index,sign in enumerate(signs):
        out[index*SLOT:index*SLOT+len(wave)] = sign*wave
    return out


def feedback_burst(status,sequence):
    wave = feedback_wave(status,sequence)
    out = np.zeros(24_000,dtype=np.complex64)  # finite 3 ms cyclic demo
    out[800:800+len(wave)] = wave
    return out


def _correlation(a,b):
    return np.vdot(a,b)/np.sqrt(max(float(np.vdot(a,a).real*np.vdot(b,b).real),1e-30))


def _candidate(x,start,minimum_score=.85,phase_tolerance=.5):
    if start<0 or start+FRAME_SAMPLES>len(x):
        return None
    segments = [x[start+k*SLOT:start+k*SLOT+128] for k in range(SLOTS)]
    ref = segments[0]
    lag = 8
    coarse = float(np.angle(np.sum(ref[lag:]*np.conj(ref[:-lag])))*FS/(2*np.pi*lag))
    across = _correlation(ref,segments[2])
    if abs(across)<minimum_score:
        return None
    distance = 2*SLOT/FS
    residual = np.angle(across*np.exp(-2j*np.pi*coarse*distance))
    frequency = coarse+float(residual)/(2*np.pi*distance)
    correlations = [_correlation(ref,segments[k])*np.exp(-2j*np.pi*frequency*k*SLOT/FS) for k in (1,2)]
    if any(abs(c)<minimum_score for c in correlations):
        return None
    if abs(np.angle(-correlations[0]))>phase_tolerance or abs(np.angle(correlations[1]))>phase_tolerance:
        return None
    if _shape_score(ref,lag)<.94:
        return None
    value = 0
    scores = []
    for pair in range(18):
        index = 3+2*pair
        c = _correlation(segments[index],segments[index+1])*np.exp(-2j*np.pi*frequency*SLOT/FS)
        # Both polarity and phase margin are tested, not merely the sign.
        score = abs(float(c.real))
        if score<minimum_score or abs(np.angle(c if c.real>=0 else -c))>phase_tolerance:
            return None
        value = (value<<1)|int(c.real<0)
        scores.append(score)
    status,sequence,check = value>>16,(value>>8)&255,value&255
    if crc8(bytes([0xA7,status,sequence]))!=check:
        return None
    return dict(start_sample=start,end_sample=start+FRAME_SAMPLES,
        status=status,status_text=STATUS[status],sequence=sequence,crc_ok=True,
        crc_hex=f'{check:02x}',residual_cfo_hz=frequency,
        minimum_pair_score=min(scores),time_ms=start/FS*1000)


def decode(iq):
    x = np.asarray(iq,dtype=np.complex128)
    if x.ndim!=1 or not np.isfinite(x).all():
        raise ValueError('Expected finite one-dimensional IQ')
    if len(x)<FRAME_SAMPLES:
        return []
    # Packet slots have 4 us idle guards. Acquire their unknown edges from IQ;
    # the standalone presence detector may merge adjacent ORS into one group.
    power = np.abs(x)**2
    noise = max(float(np.quantile(power,.2)/(-np.log(.8))),1e-20)
    active = np.flatnonzero(power>6*noise)
    if not len(active):
        return []
    groups = np.split(active,np.flatnonzero(np.diff(active)>4)+1)
    seeds = [int(group[0]) for group in groups if len(group)>=80]
    result = []
    for seed in seeds:
        if any(frame['start_sample']-16<=seed<frame['end_sample'] for frame in result):
            continue
        variants = []
        for offset in range(-8,9):
            frame = _candidate(x,seed+offset)
            if frame is not None:
                variants.append(frame)
        if variants:
            result.append(max(variants,key=lambda frame:frame['minimum_pair_score']))
    return result
