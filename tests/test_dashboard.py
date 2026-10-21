import http.client
import json
from pathlib import Path
import threading
import pytest
import dashboard_server as dashboard


@pytest.fixture
def server():
    server = dashboard.create_server(0)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(server,method,path,data=None,token=True,origin=None,host=None):
    connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
    headers = {}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if token:
        headers['X-CTC-Token'] = server.token
    if origin:
        headers['Origin'] = origin
    if host:
        headers['Host'] = host
    connection.request(method,path,body=json.dumps(data) if data is not None else None,headers=headers)
    response = connection.getresponse()
    status = response.status
    body = response.read()
    connection.close()
    return status,body


def test_page_and_real_report_schema(server):
    status,body = request(server,'GET','/')
    assert status==200 and '跨技术通信实验台'.encode() in body
    status,body = request(server,'GET','/api/state')
    data = json.loads(body)
    assert status==200
    assert data['transmitting'] is False
    assert data['report']['data']['matched_unique_frames']==3
    assert data['summary']['commodity_zigbee_confirmed'] is False


def test_mutating_requests_need_token_and_same_origin(server):
    assert request(server,'POST','/api/jobs',{'action':'devices'},token=False)[0]==403
    assert request(server,'POST','/api/jobs',{'action':'devices'},origin='https://example.org')[0]==403
    assert request(server,'GET','/api/state',host='example.org')[0]==403


def test_parameter_validation_never_launches_unknown_or_invalid_commands(server):
    assert request(server,'POST','/api/jobs',{'action':'arbitrary-command'})[0]==400
    assert request(server,'POST','/api/jobs',{'action':'rf','params':{'uri':'usb:1.5.5','seconds':999}})[0]==400
    assert request(server,'POST','/api/jobs',{'action':'simulate','params':{'snr':'NaN'}})[0]==400
    assert server.jobs.job is None


def test_report_path_cannot_escape_artifacts(server):
    assert request(server,'GET','/api/report?run=../../README.md')[0]==400
    assert request(server,'GET','/../README.md')[0]==404
    assert request(server,'GET','/api/report?run=20261020_000000_000000_missing')[0]==400


def test_whitelisted_command_arguments_are_passed_without_shell():
    command = dashboard.command_for('simulate',{'snr':24,'cfo':12000,'clock':10})
    assert command[1:3]==['ctc_run.py','simulate']
    assert '--snr' in command
    with pytest.raises(ValueError):
        dashboard.command_for('rf',{'uri':'usb:1.5.5; malicious'})


def test_cooperative_stop_only_writes_current_new_rf_directory(tmp_path,monkeypatch):
    artifacts = tmp_path/'artifacts'
    artifacts.mkdir()
    previous = artifacts/'old_run'
    previous.mkdir()
    current = artifacts/'new_run'
    current.mkdir()
    monkeypatch.setattr(dashboard,'ARTIFACTS',artifacts)
    manager = dashboard.JobManager()
    manager.job = {'before':{'old_run'}}
    (artifacts/'ctc_active.json').write_text(json.dumps({'run_directory':str(previous)}))
    manager._rf_stop()
    assert not (previous/'STOP').exists()
    (artifacts/'ctc_active.json').write_text(json.dumps({'run_directory':str(current)}))
    manager._rf_stop()
    assert (current/'STOP').is_file()


def test_parallel_job_is_rejected_before_spawning():
    manager = dashboard.JobManager()
    manager.job = {'status':'running'}
    with pytest.raises(RuntimeError):
        manager.start('symbols',{})
