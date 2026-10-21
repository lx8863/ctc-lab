import numpy as np
import pytest
import json
from argparse import Namespace
from ctc_lab.ors import ack_wave,burst
from ctc_lab.ors_receiver import detect
from ctc_lab.channel import impair


@pytest.mark.parametrize('pair',[(1,1),(0,1),(0,0),(1,0)])
def test_chip_wave_repeats_for_each_ack_type(pair):
    wave = ack_wave(pair=pair)
    assert len(wave)==128
    assert np.allclose(wave[8:],wave[:-8],atol=1e-7)


@pytest.mark.parametrize('rotation',[0,np.pi/2,np.pi,1.2345])
def test_blind_presence_is_independent_of_absolute_phase(rotation):
    iq,_ = burst()
    captured = impair(iq*np.exp(1j*rotation),8_000_000,snr_db=20,cfo_hz=30000,
                      clock_ppm=20,delay_samples=183.75,seed=810)
    result = detect(captured)
    assert len(result['detections'])==1
    assert abs(result['detections'][0]['start_sample']-4183.75)<128
    assert result['ack_type_decoded'] is False
    assert result['detections'][0]['ack_type'] is None
    assert result['crc_applicable'] is False


def test_no_schedule_or_preknown_start_and_cropped_edges():
    wave = ack_wave()
    iq = np.zeros(21000,dtype=complex)
    iq[93:93+len(wave)] = wave
    iq[17300:17300+len(wave)] = wave*np.exp(.7j)
    captured = impair(iq,8_000_000,snr_db=24,cfo_hz=-50000,delay_samples=0,seed=811)
    assert len(detect(captured)['detections'])==2
    assert detect(captured[90:150])['detections']==[]


def test_noise_silence_and_tone_rejection():
    rng = np.random.default_rng(812)
    assert detect(np.zeros(16000,dtype=complex))['detections']==[]
    noise = (rng.normal(size=16000)+1j*rng.normal(size=16000))
    assert detect(noise)['detections']==[]
    tone = np.pad(np.exp(2j*np.pi*12000*np.arange(1000)/8_000_000),(4000,4000))
    assert detect(impair(tone,8_000_000,snr_db=24,seed=813))['detections']==[]


def test_bad_iq_and_parameters():
    with pytest.raises(ValueError):
        detect(np.array([complex(np.nan)]))
    with pytest.raises(ValueError):
        detect(np.zeros((2,1000)))
    with pytest.raises(ValueError):
        detect(np.zeros(1000),minimum_ors=15)


def test_background_never_transmits_and_closes_radio(tmp_path,monkeypatch):
    import ors_run
    import ctc_lab.radio
    closed = []
    class ReceiveOnly:
        def __init__(self,*args):
            self.settings = {}
        def __enter__(self):
            return self
        def __exit__(self,*args):
            closed.append(True)
        def transmit(self,iq):
            pytest.fail('Background control must not transmit')
        def capture(self,samples,blocks):
            return [np.zeros(samples,dtype=complex)],[dict(samples=samples)]
    (tmp_path/'artifacts').mkdir()
    run = tmp_path/'artifacts'/'control'
    run.mkdir()
    monkeypatch.setattr(ors_run,'ROOT',tmp_path)
    monkeypatch.setattr(ors_run,'new_run',lambda _:run)
    monkeypatch.setattr(ctc_lab.radio,'Radio',ReceiveOnly)
    args = Namespace(mode='background',uri='usb:1.5.5',seconds=0,blocks=1,tx_gain=-25,rx_gain=20)
    assert ors_run.experiment(args)==0
    assert closed==[True]
    report = json.loads((run/'report.json').read_text(encoding='utf-8'))
    assert report['tx_enabled'] is False
    assert report['negative_control'] is True
    assert report['ack_present'] is False


def test_ors_jobs_are_whitelisted_and_rf_stop_is_cooperative(tmp_path,monkeypatch):
    import dashboard_server as dashboard
    assert dashboard.command_for('ors_simulate',{})[1:4]==['ors_run.py','--mode','simulate']
    command = dashboard.command_for('ors_rf',dict(uri='usb:1.5.5',seconds=1,blocks=1))
    assert command.count('rf')==1
    assert '--data' not in command
    run = tmp_path/'new'
    run.mkdir()
    (tmp_path/'ctc_active.json').write_text(json.dumps(dict(run_directory=str(run))))
    monkeypatch.setattr(dashboard,'ARTIFACTS',tmp_path)
    manager = dashboard.JobManager()
    manager.job = dict(action='ors_rf',status='running',before=set(),logs=[],stop_requested=False,clock=0)
    manager.stop()
    assert (run/'STOP').exists()
