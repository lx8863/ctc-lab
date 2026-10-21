"""Loopback-only CTC control room; report viewer and constrained job launcher."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone,timedelta
from functools import lru_cache
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse,parse_qs,quote

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT/'artifacts'
WEB = ROOT/'dashboard'
TZ = timezone(timedelta(hours=8))
LABELS = {'symbols':'符号映射验证','simulate':'完整事件仿真','validate':'70 帧扰动验证',
          'devices':'检测 SDR','rf':'限时射频测试',
          'send_simulate':'仅仿真并解析','send_rf':'硬件 RF 发送并解析',
          'ors_simulate':'ORS ACK 仿真检测','ors_rf':'ORS ACK 硬件检测',
          'ors_background':'ORS 接收背景对照','ors_validate':'ORS 扰动与负样本验证',
          'feedback_types_simulate':'四类 ACK 仿真','feedback_types_rf':'四类 ACK 硬件测试',
          'feedback_loop_simulate':'丢 ACK 重传仿真','feedback_loop_rf':'数据与 ACK 硬件闭环',
          'feedback_background':'ACK 帧接收背景','feedback_validate':'参考 ACK 扰动验证'}


def read_json(path,default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        return default


def run_path(run):
    if not isinstance(run,str) or not re.fullmatch(r'\d{8}_\d{6}_\d{6}_[a-zA-Z0-9_]+',run):
        raise ValueError('无效的实验编号')
    path = (ARTIFACTS/run).resolve()
    if path.parent!=ARTIFACTS.resolve() or not (path/'report.json').is_file():
        raise ValueError('实验报告不存在')
    return path


def report_index():
    paths = sorted(ARTIFACTS.glob('*ctc*/report.json'),reverse=True)
    rows = []
    for path in paths[:60]:
        data = read_json(path,{})
        run = path.parent.name
        try:
            stamp = datetime.strptime(run[:15],'%Y%m%d_%H%M%S').replace(tzinfo=TZ).isoformat()
        except ValueError:
            continue
        mode = data.get('mode','')
        category = ('rf' if ('RF' in mode or 'rf' in mode) else
                    'symbols' if 'symbol' in mode else
                    'validate' if 'sensitivity' in mode else 'simulate')
        rows.append(dict(id=run,date=stamp,category=category,mode=mode,
                         correct=data.get('matched_unique_frames'),
                         external_ble=data.get('external_ble_confirmed',False)))
    return rows


def selected_report(run=None):
    summary = read_json(ARTIFACTS/'ctc_summary.json',{})
    if run:
        path = run_path(run)
    else:
        preferred = Path(summary.get('comprehensive_report',''))
        if preferred.is_file() and preferred.resolve().is_relative_to(ARTIFACTS.resolve()):
            path = preferred.parent
        else:
            index = report_index()
            if not index:
                return None
            path = run_path(index[0]['id'])
    data = read_json(path/'report.json',{})
    return dict(id=path.name,data=data)


def _number(params,key,default,lo,hi,integer=False):
    try:
        value = float(params.get(key,default))
    except (ValueError,TypeError) as exc:
        raise ValueError(f'{key} 必须为数字') from exc
    if not math.isfinite(value) or not lo<=value<=hi or (integer and value!=int(value)):
        raise ValueError(f'{key} 必须在 {lo}～{hi} 范围内')
    return str(int(value) if integer else value)


def command_for(action,params):
    python = sys.executable
    if Path(python).name.lower() == 'pythonw.exe':
        python = str(Path(python).with_name('python.exe'))
    if action=='feedback_validate':
        return [python,'validate_feedback.py']
    feedback_modes = {'feedback_types_simulate':'types-simulate','feedback_types_rf':'types-rf',
        'feedback_loop_simulate':'loop-simulate','feedback_loop_rf':'loop-rf','feedback_background':'background'}
    if action in feedback_modes:
        mode = feedback_modes[action]
        command = [python,'feedback_run.py','--mode',mode]
        if mode.startswith('loop-'):
            from send_receive import parse_payload
            data = params.get('data')
            if not isinstance(data,str):
                raise ValueError('请输入闭环数据')
            payload = parse_payload('ctc',params.get('encoding'),data)
            command += ['--encoding','hex','--data',payload.hex(),
                '--attempts',_number(params,'attempts',3,1,5,integer=True),
                '--ack-timeout',_number(params,'ack_timeout',10,1,30)]
            if mode=='loop-simulate':
                command += ['--drop-first-ack']
        if mode.endswith('-rf') or mode=='background':
            rf_args = command_for('rf',params)[3:]
            # Each exchange stops immediately after capture; no advertising hold.
            command += [value for index in range(0,len(rf_args),2) if rf_args[index]!='--seconds'
                        for value in rf_args[index:index+2]]
        else:
            command += ['--snr',_number(params,'snr',24,5,60),
                '--cfo',_number(params,'cfo',12000,-100000,100000),
                '--clock',_number(params,'clock',10,-100,100)]
        return command
    if action=='ors_validate':
        return [python,'validate_ors.py']
    if action in ('ors_simulate','ors_rf','ors_background'):
        mode = action.removeprefix('ors_')
        command = [python,'ors_run.py','--mode',mode]
        if mode in ('rf','background'):
            return command+command_for('rf',params)[3:]
        return command+['--snr',_number(params,'snr',24,5,60),
                        '--cfo',_number(params,'cfo',12000,-100000,100000),
                        '--clock',_number(params,'clock',10,-100,100)]
    if action in ('send_simulate', 'send_rf'):
        from send_receive import parse_payload
        protocol, encoding, data = params.get('protocol'), params.get('encoding'), params.get('data')
        if not isinstance(data, str):
            raise ValueError('请输入发送数据')
        payload = parse_payload(protocol, encoding, data)
        # Always pass canonical HEX to avoid option-looking text becoming CLI flags.
        command = [python,'send_receive.py','--protocol',protocol,'--encoding','hex','--data',payload.hex(),
                   '--mode','rf' if action=='send_rf' else 'simulate']
        if action=='send_rf':
            rf_args = command_for('rf', params)[3:]
            return command+rf_args
        return command+['--snr',_number(params,'snr',24,5,60),
                        '--cfo',_number(params,'cfo',12000,-100000,100000),
                        '--clock',_number(params,'clock',10,-100,100)]
    if action=='symbols':
        return [python,'ctc_run.py','symbols']
    if action=='validate':
        return [python,'validate_ctc.py']
    if action=='devices':
        return [python,'-m','ctc_lab','devices']
    if action=='simulate':
        return [python,'ctc_run.py','simulate','--snr',_number(params,'snr',24,5,60),
                '--cfo',_number(params,'cfo',12000,-100000,100000),
                '--clock-ppm',_number(params,'clock',10,-100,100)]
    if action=='rf':
        uri = params.get('uri','')
        if not isinstance(uri,str) or not re.fullmatch(r'usb:\d+\.\d+\.\d+',uri):
            raise ValueError('请先检测并选择 USB SDR')
        return [python,'ctc_run.py','rf','--uri',uri,
            '--tx-gain',_number(params,'tx_gain',-25,-60,-10),
            '--rx-gain',_number(params,'rx_gain',20,0,50),
            '--seconds',_number(params,'seconds',5,0,60),
            '--blocks',_number(params,'blocks',4,1,6,integer=True)]
    raise ValueError('不支持的操作')


class JobManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.job = None
        self.proc = None
        self.devices = {}
        self.device_checked_at = None

    def snapshot(self):
        with self.lock:
            if not self.job:
                return None
            result = {key:value for key,value in self.job.items() if key not in ('before','command')}
            result['logs'] = list(self.job['logs'])
            result['elapsed'] = round(time.monotonic()-self.job['clock'],1) if self.job['status']=='running' else self.job.get('elapsed',0)
            result.pop('clock',None)
            return result

    def start(self,action,params):
        command = command_for(action,params)
        with self.lock:
            if self.job and self.job['status']=='running':
                raise RuntimeError('已有任务运行，请等待结束或先停止')
            self.job = dict(id=secrets.token_hex(5),action=action,label=LABELS[action],
                status='running',clock=time.monotonic(),started_at=datetime.now(TZ).isoformat(),
                stop_requested=False,logs=[],before={p.name for p in ARTIFACTS.iterdir() if p.is_dir()},
                command=command,report_id=None,exit_code=None)
            threading.Thread(target=self._work,args=(self.job,),daemon=True).start()
            return self.snapshot()

    def _rf_stop(self):
        state = read_json(ARTIFACTS/'ctc_active.json',{})
        path = Path(state.get('run_directory','')).resolve()
        if (path.parent==ARTIFACTS.resolve() and path.name not in self.job['before']
                and path.is_dir()):
            (path/'STOP').write_text('dashboard-stop\n',encoding='utf-8')

    def stop(self):
        with self.lock:
            if not self.job or self.job['status']!='running':
                return self.snapshot()
            self.job['stop_requested'] = True
            if self.job['action'] in ('rf','send_rf','ors_rf','ors_background',
                                     'feedback_types_rf','feedback_loop_rf','feedback_background'):
                self._rf_stop()
            elif self.proc:
                self.proc.terminate()
            return self.snapshot()

    def _work(self,job):
        try:
            proc = subprocess.Popen(job['command'],cwd=str(ROOT),
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.proc = proc
            def monitor():
                while proc.poll() is None:
                    with self.lock:
                        if self.job is not job:
                            return
                        if job['stop_requested']:
                            if job['action'] in ('rf','send_rf','ors_rf','ors_background',
                                                 'feedback_types_rf','feedback_loop_rf','feedback_background'):
                                self._rf_stop()
                            else:
                                proc.terminate()
                    time.sleep(0.2)
            threading.Thread(target=monitor,daemon=True).start()
            for line in self.proc.stdout:
                with self.lock:
                    self.job['logs'].append(line.rstrip())
                    self.job['logs'] = self.job['logs'][-350:]
            code = self.proc.wait()
            with self.lock:
                self.job['exit_code'] = code
                self.job['status'] = ('stopped' if self.job['stop_requested'] else
                                      'complete' if code==0 else 'incomplete' if code==2 else 'failed')
                self.job['elapsed'] = round(time.monotonic()-self.job['clock'],1)
                new_runs = sorted(p for p in ARTIFACTS.iterdir() if p.is_dir() and
                    p.name not in self.job['before'] and (p/'report.json').exists())
                if new_runs:
                    self.job['report_id'] = new_runs[-1].name
                if self.job['action']=='devices' and code==0:
                    raw = '\n'.join(self.job['logs'])
                    try:
                        self.devices = json.loads(raw[raw.index('{'):raw.rindex('}')+1])
                        self.device_checked_at = datetime.now(TZ).isoformat()
                    except (ValueError,TypeError):
                        self.devices = {}
        except Exception as exc:
            with self.lock:
                self.job['status'] = 'failed'
                self.job['logs'].append(str(exc))
                self.job['elapsed'] = round(time.monotonic()-self.job['clock'],1)


def spectrum_data(run):
    selected = selected_report(run)
    if not selected:
        return dict(available=False)
    data = selected['data']
    directory = run_path(selected['id'])
    source = data.get('source_rf_run')
    if source:
        directory = Path(source).resolve()
        if not directory.is_relative_to(ARTIFACTS.resolve()):
            return dict(available=False)
    captures = sorted(directory.glob('rx*.npy'))
    if not captures:
        captures = sorted(directory.glob('*_rx_*.npy'))
    if not captures:
        return dict(available=False,reason='此实验没有保存接收 IQ')
    path = captures[0].resolve()
    return _spectrum(str(path),path.stat().st_mtime_ns)


@lru_cache(maxsize=10)
def _spectrum(filename,stamp):
    import numpy as np
    from scipy.signal import welch
    path = Path(filename)
    metadata = read_json(path.with_suffix('.json'),{})
    fs = metadata.get('sample_rate',8_000_000)
    center = metadata.get('lo_hz',metadata.get('settings',{}).get('frequency_hz',2_428_000_000))
    samples = np.load(path,mmap_mode='r',allow_pickle=False)
    if samples.ndim!=1 or not np.iscomplexobj(samples) or len(samples)<2048:
        return dict(available=False,reason='IQ 数据格式不支持频谱显示')
    frequency,power = welch(samples[:1_048_576],fs=fs,nperseg=2048,
        noverlap=1024,return_onesided=False,scaling='density')
    power = np.fft.fftshift(power)
    frequency = np.fft.fftshift(frequency)
    relative = 10*np.log10(np.maximum(power,1e-30)/max(float(np.max(power)),1e-30))
    return dict(available=True,frequency_mhz=((frequency[::4]+center)/1e6).round(5).tolist(),
        power_db=relative[::4].round(3).tolist(),source_run=path.parent.name,
        sample_rate=fs,samples=min(len(samples),1_048_576),source=metadata.get('source','saved-IQ'),
        label='保存的接收 IQ · 相对峰值功率',physical='physical' in metadata.get('source','').lower())


class Handler(BaseHTTPRequestHandler):
    server_version = 'CTCLab/1.0'

    def log_message(self,*args):
        pass

    def _json(self,data,status=200):
        self._bytes(json.dumps(data,ensure_ascii=False).encode('utf-8'),'application/json; charset=utf-8',status)

    def _bytes(self,body,mime,status=200,download=None):
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if download:
            self.send_header('Content-Disposition',f"attachment; filename*=UTF-8''{quote(download)}")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        port = self.server.server_port
        return self.headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}')

    def do_GET(self):
        if not self._host_ok():
            return self._json({'error':'仅允许本机访问'},403)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path in ('/','/index.html','/app.js','/style.css'):
                file = WEB/('index.html' if url.path in ('/','/index.html') else url.path[1:])
                mime = 'text/html; charset=utf-8' if file.suffix=='.html' else 'text/javascript; charset=utf-8' if file.suffix=='.js' else 'text/css; charset=utf-8'
                return self._bytes(file.read_bytes(),mime)
            run = query.get('run',[None])[0]
            if url.path=='/api/state':
                job = self.server.jobs.snapshot()
                active = read_json(ARTIFACTS/'ctc_active.json',{})
                own_tx = bool(job and job['status']=='running' and job['action']=='rf' and
                              active.get('status')=='transmitting' and
                              Path(active.get('run_directory','')).name not in self.server.jobs.job['before'])
                return self._json(dict(token=self.server.token,summary=read_json(ARTIFACTS/'ctc_summary.json',{}),
                    report=selected_report(run),reports=report_index(),job=job,
                    devices=self.server.jobs.devices,device_checked_at=self.server.jobs.device_checked_at,
                    transmitting=own_tx,server_time=datetime.now(TZ).isoformat()))
            if url.path=='/api/spectrum':
                return self._json(spectrum_data(run))
            if url.path=='/api/report':
                selected = selected_report(run)
                if not selected:
                    raise ValueError('没有报告')
                return self._bytes(json.dumps(selected['data'],ensure_ascii=False,indent=2).encode('utf-8'),
                    'application/json; charset=utf-8',download=selected['id']+'.json')
            if url.path=='/api/document':
                return self._bytes((ROOT/'CTC阶段报告.md').read_bytes(),
                    'text/markdown; charset=utf-8',download='CTC阶段报告.md')
            return self._json({'error':'页面不存在'},404)
        except (ValueError,OSError) as exc:
            self._json({'error':str(exc)},400)

    def do_POST(self):
        # Drain the bounded body before rejection; unread bytes otherwise cause
        # Windows to reset the connection before the browser receives the 403.
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:
                raise ValueError('请求过大或为空')
            raw_body = self.rfile.read(size)
        except ValueError as exc:
            return self._json({'error':str(exc)},400)
        if not self._host_ok():
            return self._json({'error':'仅允许本机访问'},403)
        origin = self.headers.get('Origin')
        own_origins = (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}')
        if (origin and origin not in own_origins) or self.headers.get('X-CTC-Token')!=self.server.token:
            return self._json({'error':'请从本机实验页面发起操作'},403)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self._json({'error':'请求格式错误'},400)
        try:
            data = json.loads(raw_body)
            if not isinstance(data,dict):
                raise ValueError('请求必须为对象')
            if self.path=='/api/jobs':
                action = data.get('action')
                params = data.get('params',{})
                if not isinstance(params,dict):
                    raise ValueError('参数格式错误')
                return self._json(self.server.jobs.start(action,params),202)
            if self.path=='/api/stop':
                return self._json(self.server.jobs.stop())
            return self._json({'error':'不支持的操作'},404)
        except (ValueError,TypeError) as exc:
            self._json({'error':str(exc)},400)
        except RuntimeError as exc:
            self._json({'error':str(exc)},409)


def create_server(port=8765):
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.token = secrets.token_urlsafe(32)
    server.jobs = JobManager()
    return server


def main():
    sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',type=int,default=8765)
    args = p.parse_args()
    server = create_server(args.port)
    print(f'CTC Lab: http://127.0.0.1:{server.server_port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.jobs.stop()
        # RF STOP is cooperative; give hardware subprocess time to close buffers.
        deadline = time.monotonic()+35
        while server.jobs.snapshot() and server.jobs.snapshot()['status']=='running' and time.monotonic()<deadline:
            time.sleep(0.2)
        server.server_close()


if __name__=='__main__':
    main()
