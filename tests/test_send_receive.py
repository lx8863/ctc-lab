from argparse import Namespace
import json
import numpy as np
import pytest
import send_receive as sender
from dashboard_server import command_for


@pytest.mark.parametrize('protocol,payload', [
    ('ble', bytes.fromhex('00ff1020304050607080')),
    ('ieee802154', bytes(range(116))),
    ('ctc', bytes(range(38))),
    ('ctc', '你好 CTC'.encode('utf-8')),
    ('esb', bytes(range(32))),
])
def test_payload_round_trip_through_iq(protocol, payload):
    iq, _, _ = sender.prepare(protocol, payload)
    if protocol in ('ble','ctc'):
        iq = iq[:24000]
    from ctc_lab.channel import impair
    captured = impair(iq, 8_000_000, snr_db=24, cfo_hz=12000,
                      clock_ppm=10, delay_samples=173.3, seed=7)
    decoded = sender.decode_capture(protocol, captured)
    rows = sender.compare(decoded, protocol, payload)
    assert any(r['matches_sent'] and r['received_hex']==payload.hex() for r in rows)
    assert not any(r['matches_sent'] for r in sender.compare(decoded, protocol, b'wrong'))


@pytest.mark.parametrize('protocol', sender.LIMITS)
def test_invalid_payload_rejected_before_job(protocol):
    with pytest.raises(ValueError):
        sender.parse_payload(protocol, 'hex', 'xyz')
    with pytest.raises(ValueError):
        sender.parse_payload(protocol, 'utf8', '')
    with pytest.raises(ValueError):
        sender.parse_payload(protocol, 'utf8', 'x'*(sender.LIMITS[protocol]+1))


def test_cli_arguments_are_canonical_not_interpreted_as_options():
    params = dict(protocol='ctc', encoding='utf8', data='--help')
    command = command_for('send_simulate', params)
    assert command[command.index('--data')+1] == b'--help'.hex()
    command = command_for('send_rf', dict(params, uri='usb:1.5.5', seconds=1, blocks=1))
    assert command[command.index('--mode')+1]=='rf'
    assert '--seconds' in command
    assert command.count('rf')==1


def test_noise_does_not_report_sent_payload_as_received():
    rng = np.random.default_rng(8)
    iq = (rng.normal(size=24000)+1j*rng.normal(size=24000)).astype(np.complex64)
    for protocol in sender.LIMITS:
        assert sender.compare(sender.decode_capture(protocol, iq), protocol, b'hello')==[]


def test_rf_window_covers_advertising_event_even_after_missing_its_start():
    payload = b'Hello CTC'
    iq, _, _ = sender.prepare('ctc', payload)
    # Begin after the first event: a 32 ms block sees only silence, but the
    # corrected block must contain the next full event without TX timing input.
    start = 24000
    stream = np.tile(iq, 3)
    short = sender.decode_capture('ctc', stream[start:start+262144])
    assert sender.compare(short, 'ctc', payload)==[]
    captured = stream[start:start+sender.RF_CAPTURE_SAMPLES]
    assert sender.RF_CAPTURE_SAMPLES/8_000_000 > .107375+.003
    rows = sender.compare(sender.decode_capture('ctc', captured), 'ctc', payload)
    assert any(row['matches_sent'] for row in rows)


def test_rf_failure_releases_radio_and_records_stopped_state(tmp_path, monkeypatch):
    import ctc_lab.radio
    closed = []
    class BrokenRadio:
        def __init__(self, *a):
            self.settings = {}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            closed.append(True)
        def transmit(self, iq):
            raise RuntimeError('injected failure')
    monkeypatch.setattr(ctc_lab.radio, 'Radio', BrokenRadio)
    (tmp_path/'artifacts').mkdir()
    run = tmp_path/'artifacts'/'run'
    run.mkdir()
    monkeypatch.setattr(sender, 'ROOT', tmp_path)
    monkeypatch.setattr(sender, 'new_run', lambda _:run)
    args = Namespace(protocol='ble',encoding='utf8',data='test',mode='rf',uri='usb:1.5.5',
                     tx_gain=-25,rx_gain=20,seconds=1,blocks=1)
    with pytest.raises(RuntimeError, match='injected failure'):
        sender.experiment(args)
    assert closed==[True]
    assert json.loads((run/'status.json').read_text())['status']=='failed'
