"""Reproducible synthetic channel; never substitutes for RF measurements."""
from __future__ import annotations
import numpy as np


def impair(iq: np.ndarray, sample_rate: int, snr_db: float = 20.,
           cfo_hz: float = 0., phase: float = 0.7, delay_samples: float = 173.25,
           clock_ppm: float = 0., seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if not all(np.isfinite(x) for x in (snr_db,cfo_hz,phase,delay_samples,clock_ppm)):
        raise ValueError("Channel parameters must be finite")
    if delay_samples < 0 or abs(clock_ppm) > 1000:
        raise ValueError("Need nonnegative delay and clock error <=1000 ppm")
    ratio = 1+clock_ppm*1e-6
    length = int(np.ceil(len(iq)*ratio+delay_samples))+100
    positions = (np.arange(length)-delay_samples)/ratio
    x = np.arange(len(iq))
    shifted = np.interp(positions,x,iq.real,left=0,right=0)+1j*np.interp(
        positions,x,iq.imag,left=0,right=0)
    shifted *= np.exp(1j*(phase+2*np.pi*cfo_hz*np.arange(length)/sample_rate))
    active = np.abs(iq)>0.1
    signal_power = float(np.mean(np.abs(iq[active])**2)) if np.any(active) else 1.
    sigma = np.sqrt(signal_power*10**(-snr_db/10)/2)
    shifted += sigma*(rng.standard_normal(length)+1j*rng.standard_normal(length))
    return shifted.astype(np.complex64)
