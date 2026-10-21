"""Held-seed ORS presence checks; a mechanism validation, not paper figure reproduction."""
import json
import sys
import numpy as np
from ctc_lab.ors import ack_wave
from ctc_lab.ors_receiver import detect
from ctc_lab.channel import impair
from ctc_lab.storage import new_run,write_json
from send_receive import prepare


def run():
    positives = []
    seed = 91000  # Held-out seeds, separate from initial detector development.
    for snr in (12,20,30):
        for cfo in (-50000,0,50000):
            for clock in (-20,20):
                for offset in range(8):
                    for pair in ((1,1),(0,1),(0,0),(1,0)):
                        rng = np.random.default_rng(seed)
                        wave = ack_wave(pair=pair)*np.exp(1j*rng.uniform(-np.pi,np.pi))
                        # Start is randomly placed, unknown to detector.
                        lead = int(rng.integers(400,2000))
                        iq = np.pad(wave,(lead,6000-lead))
                        captured = impair(iq,8_000_000,snr_db=snr,cfo_hz=cfo,clock_ppm=clock,
                                          delay_samples=offset+.37,seed=seed)
                        detections = detect(captured)['detections']
                        expected = lead+offset+.37
                        correct = len(detections)==1 and abs(detections[0]['start_sample']-expected)<=128
                        positives.append(dict(seed=seed,snr_db=snr,cfo_hz=cfo,clock_ppm=clock,
                                              fractional_offset=offset+.37,passed=bool(correct),detections=len(detections)))
                        seed += 1
    negatives = []
    rng = np.random.default_rng(92000)
    signals = {}
    for protocol in ('ble','ieee802154','esb','ctc'):
        for label,payload in [('zeros',bytes(6)),('ones',b'\xff'*6),('text',b'hello')]:
            iq,_,_ = prepare(protocol,payload)
            signals[protocol+'_'+label] = iq[:24000]
    n = np.arange(24000)
    for frequency in (0,12000,50000,1_000_000):
        signals['tone_'+str(frequency)] = np.exp(2j*np.pi*frequency*n/8_000_000)
    for seed in range(20):
        signals['noise_'+str(seed)] = (rng.normal(size=24000)+1j*rng.normal(size=24000))*.5
    signals['silence'] = np.zeros(24000,dtype=complex)
    signals['impulse'] = np.eye(1,24000,4000,dtype=complex).ravel()
    for label,iq in signals.items():
        if not label.startswith(('noise','silence','impulse')):
            iq = impair(iq,8_000_000,snr_db=24,cfo_hz=12000,delay_samples=173.3,seed=92000)
        found = detect(iq)['detections']
        negatives.append(dict(label=label,samples=len(iq),false_detections=len(found)))
    report = dict(experiment='ors-validation',mode='ORS-validation',seed_start=91000,negative_seed=92000,
        positive_trials=len(positives),positive_pass=sum(r['passed'] for r in positives),
        negative_trials=len(negatives),negative_pass=sum(r['false_detections']==0 for r in negatives),
        negative_total_samples=sum(r['samples'] for r in negatives),
        positives=positives,negatives=negatives,
        limitation='Finite seeded synthetic tests; no universal false-alarm guarantee, ACK-type decision or paper-curve reproduction')
    path = new_run('ctc_ors_validation')
    write_json(path/'report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('positives','negatives')},indent=2))
    print(f'Report: {path/"report.json"}')
    return 0 if report['positive_pass']==len(positives) and report['negative_pass']==len(negatives) else 2


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace')
    raise SystemExit(run())
