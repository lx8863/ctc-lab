"""Isolated ORS mechanism check, not a reproduction of paper figures or RF.

Uses raw OQPSK chips (bypasses DSSS), normalized power, and known test windows.
The detector gets samples only, not the transmitted ACK type.
"""
from pathlib import Path
import json
import numpy as np

FS = 64_000_000
SPC = FS//2_000_000
LAG = FS//1_000_000
M = 15
PAIRS = {1:(1,1),2:(0,1),3:(0,0),4:(1,0)}


def waveform(kind):
    i, q = PAIRS[kind]
    chips = np.array([i,q]*(M+1)+[i])
    impulses = np.zeros(len(chips)*SPC,dtype=complex)
    impulses[::2*SPC] = (2*chips[::2]-1)
    impulses[SPC::2*SPC] = 1j*(2*chips[1::2]-1)
    pulse = np.sin(np.pi*np.arange(2*SPC)/(2*SPC))
    return np.convolve(impulses,pulse)


def detect(samples):
    differences = np.angle(samples[1:]*np.conj(samples[:-1]))
    mask = np.abs(differences)<1.0
    detected = bool(np.mean(np.abs(samples)**2)>.1 and np.count_nonzero(mask)>8)
    quadrants = np.where(samples.real>=0,np.where(samples.imag>=0,1,4),np.where(samples.imag>=0,2,3))
    votes = np.bincount(quadrants[:-1][mask],minlength=5)[1:]
    kind = int(np.argmax(votes)+1) if detected else None
    return detected, kind


def audit(rotation=0.,cfo=0.):
    detections = matches = total = 0
    maximum_phase_difference = 0.
    for kind in PAIRS:
        wave = waveform(kind)
        time = np.arange(len(wave))/FS
        wave *= np.exp(1j*(rotation+2*np.pi*cfo*time))
        for offset in np.arange(LAG)+.5:
            positions = SPC+offset+np.arange(M+1)*LAG
            samples = np.interp(positions,np.arange(len(wave)),wave.real)+1j*np.interp(positions,np.arange(len(wave)),wave.imag)
            found, inferred = detect(samples)
            maximum_phase_difference = max(maximum_phase_difference,float(np.max(np.abs(np.angle(samples[1:]*np.conj(samples[:-1]))))))
            total += 1
            detections += found
            matches += inferred==kind
    return dict(total=total,detected=detections,type_correct=matches,max_phase_difference_rad=maximum_phase_difference)


if __name__=='__main__':
    results = dict(scope='Noiseless mechanism/assumption audit; known windows; not paper performance reproduction',
        m=M,phase_threshold_rad=1.,minimum_count_strictly_greater_than=8,
        normalized_power_threshold=.1,fs=FS,offsets_per_wave=64,
        aligned_phase=audit(),unknown_phase_90_degrees=audit(np.pi/2),
        cfo_25khz=audit(cfo=25_000),cfo_200khz=audit(cfo=200_000),
        limitation='No RF, channel calibration, acquisition, multipath or noise; power threshold differs from paper physical units')
    (Path(__file__).parent/'ors_probe_results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))
