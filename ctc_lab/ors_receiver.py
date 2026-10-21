"""Blind ORS presence detector. Input is IQ and public PHY parameters only.

Paper phase/count mechanism plus explicit engineering gates: estimated noise,
normalized lag coherence, non-tone rejection and OQPSK shape consistency.
Not an exact paper decoder.
No TX manifest, event schedule, expected data or ACK type is accepted.
"""
from __future__ import annotations
import numpy as np


def _sum(values, width):
    total = np.r_[0,np.cumsum(values)]
    return total[width:]-total[:-width]


def _shape_score(segment,lag):
    """Public half-sine OQPSK geometry, not TX data or a learned template.

    Search unknown sub-sample timing, arbitrary phase and the two I/Q sign
    relations. CFO comes from received lag correlation. No ACK type is output.
    """
    phase = float(np.angle(np.sum(segment[lag:]*np.conj(segment[:-lag]))))
    corrected = segment*np.exp(-1j*phase*np.arange(len(segment))/lag)
    offset = np.arange(lag*4)/(lag*4)
    time = (np.arange(len(segment))[None,:]/lag+offset[:,None])%1
    i,q = np.abs(np.cos(np.pi*time)),np.abs(np.sin(np.pi*time))
    references = np.concatenate((i+1j*q,i-1j*q))
    energy = float(np.sum(np.abs(corrected)**2))
    return float(np.max(np.abs(references.conj()@corrected))/np.sqrt(len(segment)*energy+1e-40))


def detect(iq, sample_rate=8_000_000, repetitions=15, phase_threshold=1.,
           minimum_ors=8, power_ratio=4., coherence_threshold=.9,
           tone_threshold=.8,shape_threshold=.94):
    x = np.asarray(iq,dtype=np.complex128)
    if x.ndim!=1 or not np.isfinite(x).all():
        raise ValueError('Expected finite 1D complex IQ')
    if (not isinstance(sample_rate,int) or sample_rate<4_000_000 or sample_rate%1_000_000
        or not isinstance(repetitions,int) or not 4<=repetitions<=127
        or not 0<=minimum_ors<repetitions or not 0<phase_threshold<np.pi
        or not np.isfinite(power_ratio) or power_ratio<=1 or not 0<coherence_threshold<=1
        or not 0<tone_threshold<=1 or not 0<shape_threshold<=1):
        raise ValueError('Invalid ORS detector parameters')
    lag = sample_rate//1_000_000
    if lag%2:
        raise ValueError('Need an even sample count per microsecond')
    window = (repetitions+1)*lag
    settings = dict(repetitions=repetitions,phase_threshold_rad=phase_threshold,
        minimum_ors_strictly_greater_than=minimum_ors,power_ratio_threshold=power_ratio,
        coherence_threshold=coherence_threshold,tone_rejection_threshold=tone_threshold,
        oqpsk_shape_threshold=shape_threshold,
        lag_samples=lag,window_samples=window,
        noise_estimator='20th percentile power / -log(0.8); assumes idle noise exists',
        implementation='phase-count + adaptive power + coherence + non-tone + OQPSK shape gate')
    result = dict(detections=[],noise_power=None,settings=settings,
                  ack_type_decoded=False,crc_applicable=False)
    if len(x)<window+lag or not np.any(x):
        return result
    power = np.abs(x)**2
    noise = max(float(np.quantile(power,.2)/(-np.log(.8))),1e-20)
    result['noise_power'] = noise
    average_power = _sum(power,window)/window
    pair = x[lag:]*np.conj(x[:-lag])
    pair_count = window-lag
    coherence = np.abs(_sum(pair,pair_count))/np.sqrt(
        np.maximum(_sum(power[:-lag],pair_count)*_sum(power[lag:],pair_count),1e-40))
    # There are lag independent BLE-rate timing phases. A union bound acquisition
    # gate is followed by the actual M-comparison count at the selected timing.
    good_count = _sum((np.abs(np.angle(pair))<phase_threshold).astype(float),pair_count)
    half = lag//2
    half_pair = x[half:]*np.conj(x[:-half])
    half_count = window-half
    half_coherence = np.abs(_sum(half_pair,half_count))/np.sqrt(
        np.maximum(_sum(power[:-half],half_count)*_sum(power[half:],half_count),1e-40))
    ratio = average_power/noise
    candidate = ((ratio>=power_ratio)&(coherence>=coherence_threshold)&
                 (half_coherence<tone_threshold)&(good_count>(minimum_ors*lag)))
    indices = np.flatnonzero(candidate)
    if not len(indices):
        return result
    groups = np.split(indices,np.flatnonzero(np.diff(indices)>lag)+1)
    for group in groups:
        # Grouping is derived from received statistics, not a transmit schedule.
        quality = coherence[group]*np.minimum(ratio[group],100)
        trials = group[np.argsort(quality)[::-1][:min(16,len(group))]]
        chosen = None
        for start in trials:
            start = int(start)
            samples = x[start:start+window:lag]
            phase = np.angle(samples[1:]*np.conj(samples[:-1]))
            accepted = int(np.count_nonzero(np.abs(phase)<phase_threshold))
            if accepted<=minimum_ors:
                continue
            shape = _shape_score(x[start:start+window],lag)
            if shape<shape_threshold:
                continue
            chosen = dict(start_sample=start,end_sample=start+window,
                time_ms=start/sample_rate*1000,ors_count=accepted,
                power_above_noise_db=float(10*np.log10(ratio[start])),
                lag_coherence=float(np.clip(coherence[start],0,1)),
                half_lag_coherence=float(np.clip(half_coherence[start],0,1)),
                oqpsk_shape_score=shape,
                residual_cfo_hz=float(np.angle(np.sum(pair[start:start+pair_count]))*
                                      sample_rate/(2*np.pi*lag)),
                presence_detected=True,ack_type=None,crc_ok=None)
            break
        if chosen and not any(abs(row['start_sample']-chosen['start_sample'])<window for row in result['detections']):
            result['detections'].append(chosen)
    return result
