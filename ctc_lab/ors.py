"""GOP-ACK-inspired OQPSK ORS generator; raw chips bypass symbol-to-DSSS.

One-way presence experiment only, not a BLE packet or a timed ACK reply.
See paper_review/gop_ack/阅读与复现方案.md for paper version/assumptions.
"""
from __future__ import annotations
import numpy as np
from .ieee802154 import pulse, samples_per_chip

FREQUENCY = 2_430_000_000
REPETITIONS = 15


def ack_wave(sample_rate=8_000_000, repetitions=REPETITIONS, pair=(1,0)):
    if not 4 <= repetitions <= 127 or pair not in ((1,1),(0,1),(0,0),(1,0)):
        raise ValueError('Invalid ORS repeat count or I/Q chip pair')
    spc = samples_per_chip(sample_rate)
    if sample_rate % 1_000_000:
        raise ValueError('ORS requires an integer number of samples per microsecond')
    chips = np.array(list(pair)*(repetitions+1)+[pair[0]],dtype=np.int8)
    impulses = np.zeros(len(chips)*spc,dtype=complex)
    impulses[::2*spc] = 2*chips[::2]-1
    impulses[spc::2*spc] = 1j*(2*chips[1::2]-1)
    wave = np.convolve(impulses,pulse(sample_rate))
    # Trim the two half-microsecond edge transients. Remaining M+1 waves
    # are each 1 us, giving M overlapping pairs of identical waves.
    return wave[spc:spc+(repetitions+1)*2*spc].astype(np.complex64)


def burst(sample_rate=8_000_000):
    wave = ack_wave(sample_rate)
    iq = np.zeros(sample_rate//500,dtype=np.complex64)  # cyclic 2 ms demo
    start = sample_rate//2000
    iq[start:start+len(wave)] = wave
    return iq, dict(experiment='ors-ack-presence',frequency_hz=FREQUENCY,
        sample_rate=sample_rate,repetitions=REPETITIONS,raw_chip_pair=[1,0],
        wave_period_us=1,ack_duration_us=16,interval_seconds=.002,
        type_decoding=False,ack_reply_triggered_by_data=False,
        limitation='Periodic demonstration waveform; no valid BLE PDU, ACK type or data-triggered reply')
