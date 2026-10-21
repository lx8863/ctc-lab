from argparse import Namespace
import numpy as np
import pytest
from ctc_lab.ors_feedback import feedback_burst,feedback_wave,decode,crc8,SLOT
from ctc_lab.channel import impair
from ctc_lab.ieee802154 import build_mac,parse_mac
from feedback_run import accept_ack,receiver_reply,Transport,loop


@pytest.mark.parametrize('status',[0,1,2,3])
def test_type_and_sequence_with_arbitrary_phase(status):
    iq = impair(feedback_burst(status,201),8_000_000,snr_db=20,cfo_hz=-40000,
                phase=2.7,clock_ppm=20,delay_samples=183.6,seed=23)
    frames = decode(iq)
    assert len(frames)==1
    assert frames[0]['status']==status and frames[0]['sequence']==201
    assert frames[0]['crc_ok'] is True


def test_bad_crc_and_truncated_frame_are_rejected():
    assert crc8(b'123456789')==0xF4
    wave = feedback_wave(0,73)
    # Change a sequence-bit data slot, preserving reference/sync and the old CRC.
    index = 3+2*2+1
    wave[index*SLOT:index*SLOT+128] *= -1
    assert decode(np.pad(wave,(800,8000)))==[]
    assert decode(feedback_burst(0,73)[:3000])==[]


def test_reference_requirement_and_stale_negative_ack_rejection():
    from ctc_lab.ors import burst
    assert decode(burst()[0])==[]
    valid = decode(feedback_burst(0,73))
    assert accept_ack(valid,73)
    assert accept_ack(valid,74) is None
    assert accept_ack(decode(feedback_burst(1,73)),73) is None
    assert accept_ack([dict(status=0,sequence=73,crc_ok=False)],73) is None


def test_receiver_uses_actual_crc_and_deduplicates_delivery():
    seen = set()
    bad = bytearray(build_mac(b'hello',sequence=73))
    bad[-1] ^= 1
    assert receiver_reply([parse_mac(bytes(bad))],seen) is None
    frame = parse_mac(build_mac(b'hello',sequence=73))
    assert receiver_reply([frame],seen)['status']==0
    assert receiver_reply([frame],seen)['status']==2
    assert len(seen)==1


@pytest.mark.parametrize('drop_data,drop_ack',[(False,True),(True,False)])
def test_real_iq_simulation_loop_retries_after_injected_loss(tmp_path,drop_data,drop_ack):
    args = Namespace(mode='loop-simulate',encoding='utf8',data='Hello CTC',sequence=73,
        snr=24,cfo=12000,clock=10,attempts=3,ack_timeout=1,
        drop_first_data=drop_data,drop_first_ack=drop_ack)
    transport = Transport(args,tmp_path)
    result = loop(args,transport,tmp_path)
    assert result['completed'] is True
    assert result['retransmissions']==1
    assert result['received_payload_matches'] is True
    assert result['unique_receiver_deliveries']==1
    assert result['rounds'][0]['outcome']=='timeout'
    assert result['rounds'][1]['accepted_ack']['status']==(0 if drop_data else 2)
    if drop_data:
        assert result['rounds'][0]['reply'] is None
        assert transport.events[1]['tx_enabled'] is False


def test_retry_budget_exhausts_without_fabricating_ack(tmp_path):
    args = Namespace(mode='loop-simulate',encoding='utf8',data='test',sequence=1,
        snr=24,cfo=12000,clock=10,attempts=1,ack_timeout=1,
        drop_first_data=False,drop_first_ack=True)
    result = loop(args,Transport(args,tmp_path),tmp_path)
    assert result['completed'] is False
    assert result['rounds'][0]['accepted_ack'] is None


def test_late_valid_ack_does_not_complete_the_sender(tmp_path):
    import time
    class LateTransport(Transport):
        def exchange(self,*a,**kw):
            self.physical = False
            result = super().exchange(*a,**kw)
            self.physical = True
            self.last_tx_off = time.perf_counter()-2
            return result
    args = Namespace(mode='loop-simulate',encoding='utf8',data='test',sequence=3,
        snr=24,cfo=12000,clock=10,attempts=1,ack_timeout=1,
        drop_first_data=False,drop_first_ack=False)
    result = loop(args,LateTransport(args,tmp_path),tmp_path)
    assert result['completed'] is False
    assert result['rounds'][0]['outcome']=='late_ack'
    assert result['rounds'][0]['ack_frames']
    assert result['rounds'][0]['accepted_ack'] is None


def test_gui_job_arguments_and_cooperative_stop(tmp_path,monkeypatch):
    import dashboard_server as dashboard
    import json
    params = dict(data='Hello CTC',encoding='utf8',uri='usb:1.5.5')
    command = dashboard.command_for('feedback_loop_simulate',params)
    assert '--drop-first-ack' in command
    assert command[command.index('--data')+1]==b'Hello CTC'.hex()
    command = dashboard.command_for('feedback_loop_rf',params)
    assert '--seconds' not in command and '--drop-first-ack' not in command
    directory = tmp_path/'active'
    directory.mkdir()
    (tmp_path/'ctc_active.json').write_text(json.dumps(dict(run_directory=str(directory))))
    monkeypatch.setattr(dashboard,'ARTIFACTS',tmp_path)
    manager = dashboard.JobManager()
    manager.job = dict(action='feedback_loop_rf',status='running',before=set(),logs=[],stop_requested=False,clock=0)
    manager.stop()
    assert (directory/'STOP').exists()
