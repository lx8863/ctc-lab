"""Seeded validation of reference-paired feedback; not paper benchmark curves."""
import sys
import json
import numpy as np
from ctc_lab.ors_feedback import feedback_burst,decode,STATUS
from ctc_lab.channel import impair
from ctc_lab.storage import new_run,write_json
from send_receive import prepare


def run():
    trials = []
    seed = 20262100
    for status in STATUS:
        for phase in (0,np.pi/2,np.pi,-2.1):
            for cfo in (-50000,0,50000):
                for ppm in (-20,20):
                    for snr in (18,24):
                        rng = np.random.default_rng(seed)
                        seq = int(rng.integers(0,256))
                        x = impair(feedback_burst(status,seq),8_000_000,snr_db=snr,
                            cfo_hz=cfo,clock_ppm=ppm,phase=phase,
                            delay_samples=float(rng.uniform(10,900)),seed=seed)
                        frames = decode(x)
                        passed = len(frames)==1 and frames[0]['status']==status and frames[0]['sequence']==seq
                        trials.append(dict(status=status,sequence=seq,seed=seed,cfo_hz=cfo,
                            clock_ppm=ppm,snr_db=snr,phase=phase,passed=passed))
                        seed += 1
    negatives = []
    rng = np.random.default_rng(20262200)
    for label in ('ble','ieee802154','ctc','esb','noise','tone','silence'):
        if label in ('ble','ieee802154','ctc','esb'):
            x = prepare(label,b'hello')[0][:24000]
            x = impair(x,8_000_000,snr_db=24,cfo_hz=12000,seed=20262200)
        elif label=='noise':
            x = rng.normal(size=24000)+1j*rng.normal(size=24000)
        elif label=='tone':
            x = np.exp(2j*np.pi*12000*np.arange(24000)/8_000_000)
        else:
            x = np.zeros(24000,dtype=complex)
        negatives.append(dict(label=label,samples=len(x),false_frames=len(decode(x))))
    report = dict(experiment='ors-feedback-validation',mode='ORS-feedback-validation',
        trials=trials,positive_pass=sum(t['passed'] for t in trials),positive_total=len(trials),
        negatives=negatives,negative_pass=sum(t['false_frames']==0 for t in negatives),negative_total=len(negatives),
        limitation='Finite synthetic tests; custom phase-reference/sequence/CRC8 extension, not original paper reproduction')
    path = new_run('ctc_feedback_validation')
    write_json(path/'report.json',report)
    print(json.dumps({k:report[k] for k in ('positive_pass','positive_total','negative_pass','negative_total')},indent=2))
    print(f'Report: {path/"report.json"}')
    return 0 if report['positive_pass']==len(trials) and report['negative_pass']==len(negatives) else 2


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace')
    raise SystemExit(run())
