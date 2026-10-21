"""Phase-referenced ORS ACK types and a host-orchestrated loose-timing loop.

An engineering extension, not a full GOP-ACK reproduction or independent nodes.
"""
from __future__ import annotations
import argparse
import json
import secrets
import sys
import time
from pathlib import Path
import numpy as np
from ctc_lab.ors_feedback import feedback_burst,decode as decode_ack,STATUS
from ctc_lab.ctc_experiment import receive
from ctc_lab.ble_extended import event,LO_FREQUENCY
from ctc_lab.ieee802154 import build_mac
from ctc_lab.channel import impair
from ctc_lab.storage import ROOT,new_run,write_json,save_iq
from send_receive import parse_payload


def accept_ack(frames,sequence):
    return next((row for row in frames if row.get('crc_ok') is True and
                 row.get('sequence')==sequence and row.get('status') in (0,2)),None)


def receiver_reply(frames,seen):
    """Receiver policy gets decoded frames only, not sender sequence/payload."""
    valid = [r for r in frames if r.get('fcs_ok') is True and r.get('mac_supported') is True
             and r.get('pan')==0xCAFE and r.get('source')==1 and r.get('destination')==0xFFFF]
    if not valid:
        return None
    frame = valid[0]
    fingerprint = frame['psdu_hex']
    repeated = fingerprint in seen
    seen.add(fingerprint)
    return dict(sequence=frame['sequence'],status=2 if repeated else 0,
                payload_hex=frame['payload_hex'],duplicate=repeated)


def data_burst(payload,sequence):
    psdu = build_mac(payload,sequence=sequence)
    wave,manifest = event(psdu,did=0x300+sequence)
    iq = np.zeros(round(.103125*8_000_000),dtype=np.complex64)
    iq[160:160+len(wave)] = wave
    return iq,manifest


class Transport:
    def __init__(self,args,run):
        self.args,self.run = args,run
        self.physical = args.mode.endswith('-rf') or args.mode=='background'
        self.last_tx_off = time.perf_counter()
        self.last_tx_start = None
        self.started = time.perf_counter()
        self.events = []
        self.counter = 0

    def check_stop(self):
        if (self.run/'STOP').exists():
            raise InterruptedError('用户请求停止')

    def state(self,**fields):
        if self.physical:
            write_json(ROOT/'artifacts'/'ctc_active.json',dict(run_directory=str(self.run),**fields))

    def exchange(self,iq,frequency,stage,attempt=1,drop=False):
        self.check_stop()
        self.counter += 1
        prefix = f'{self.counter:02}_{stage}'
        started = time.perf_counter()
        self.last_tx_start = None
        if iq is not None:
            save_iq(self.run/(prefix+'_tx.npy'),iq,8_000_000,lo_hz=frequency,source=stage)
        captures,stats,settings = [],[],{}
        if self.physical:
            from ctc_lab.radio import Radio
            try:
                with Radio(self.args.uri,8_000_000,frequency,self.args.tx_gain,self.args.rx_gain,0) as radio:
                    if stage=='data':
                        radio.sdr.tx_rf_bandwidth = 6_000_000
                        radio.sdr.rx_rf_bandwidth = 6_000_000
                        radio.settings.update(tx_bandwidth=6_000_000,rx_bandwidth=6_000_000)
                    settings = radio.settings
                    self.check_stop()
                    if iq is not None:
                        radio.transmit(iq)
                        self.last_tx_start = time.perf_counter()
                    self.state(status='transmitting' if iq is not None else 'capturing',stage=stage)
                    print(f'RF {stage}: '+('发射并接收' if iq is not None else '只接收'),flush=True)
                    size = 1_048_576 if stage=='data' else 262144
                    for _ in range(self.args.blocks):
                        self.check_stop()
                        blocks,block_stats = radio.capture(size,1)
                        captures.extend(blocks)
                        stats.extend(block_stats)
                self.last_tx_off = time.perf_counter()
                self.state(status='processing',stage=stage)
            except BaseException:
                self.state(status='stopped',stage=stage)
                raise
        else:
            wave = np.asarray(iq[:24000] if stage=='data' else iq) if iq is not None else np.zeros(24000,dtype=complex)
            if drop:
                wave = np.zeros_like(wave)
            captures = [impair(wave,8_000_000,snr_db=self.args.snr,cfo_hz=self.args.cfo,
                phase=1.73+attempt*.83,clock_ppm=self.args.clock,delay_samples=173.3,
                seed=20261021+self.counter)]
            self.last_tx_off = time.perf_counter()
        for index,capture in enumerate(captures):
            save_iq(self.run/f'{prefix}_rx_{index:02}.npy',capture,8_000_000,
                lo_hz=frequency,source='physical-RF-feedback' if self.physical else 'synthetic-feedback',settings=settings)
        self.events.append(dict(stage=stage,attempt=attempt,tx_enabled=iq is not None,
            physical=self.physical,settings=settings,capture_stats=stats,
            elapsed_seconds=time.perf_counter()-started,simulation_drop_injected=bool(drop),
            host_tx_call_return_seconds=(self.last_tx_start-self.started) if self.last_tx_start is not None else None,
            host_tx_off_seconds=self.last_tx_off-self.started))
        return captures


def loop(args,transport,run):
    payload = parse_payload('ctc',args.encoding,args.data)
    sequence = args.sequence
    iq,manifest = data_burst(payload,sequence)
    write_json(run/'tx_manifest.json',manifest)
    seen = set()
    rounds = []
    completed = False
    for attempt in range(1,args.attempts+1):
        transport.check_stop()
        begin = time.perf_counter()
        captures = transport.exchange(iq,LO_FREQUENCY,'data',attempt,
            drop=args.drop_first_data and attempt==1)
        data_tx_off = transport.last_tx_off
        deadline = data_tx_off+args.ack_timeout
        # Receiver only receives IQ. TX truth is reserved for report evaluation.
        decoded = []
        for capture in captures:
            decoded.extend(receive(capture)['ieee802154_frames'])
        reply = receiver_reply(decoded,seen)
        ack_iq = feedback_burst(reply['status'],reply['sequence']) if reply else None
        ack_started = time.perf_counter()
        ack_captures = transport.exchange(ack_iq,2_430_000_000,'ack',attempt,
            drop=args.drop_first_ack and attempt==1)
        frames = []
        for block,capture in enumerate(ack_captures):
            frames.extend(dict(row,capture_block=block) for row in decode_ack(capture))
        accepted = accept_ack(frames,sequence)
        late = transport.physical and time.perf_counter()>deadline
        if accepted and not late:
            outcome = 'confirmed'
            completed = True
        else:
            outcome = 'late_ack' if accepted and late else 'timeout'
            # Simulation advances a virtual timeout; RF observes a real host deadline.
            while transport.physical and time.perf_counter()<deadline:
                transport.check_stop()
                time.sleep(min(.05,max(0,deadline-time.perf_counter())))
        rounds.append(dict(attempt=attempt,pending_sequence=sequence,
            data_fcs_pass=sum(row.get('fcs_ok') is True for row in decoded),
            received_data_frames=decoded,reply=reply,ack_frames=frames,
            accepted_ack=accepted if completed else None,outcome=outcome,
            host_rx_return_to_ack_stage_ms=(ack_started-data_tx_off)*1000,
            host_rx_return_to_ack_tx_call_return_ms=((transport.last_tx_start-data_tx_off)*1000
                if transport.last_tx_start is not None else None),
            elapsed_seconds=time.perf_counter()-begin,
            virtual_timeout_applied=not transport.physical and not completed))
        print(f'第 {attempt} 轮：FCS 正确 {rounds[-1]["data_fcs_pass"]} 帧；ACK {len(frames)} 帧；{outcome}',flush=True)
        if completed:
            break
    return dict(completed=completed,sequence=sequence,attempt_count=len(rounds),
        retransmissions=max(0,len(rounds)-1),rounds=rounds,
        unique_receiver_deliveries=len(seen),sent_hex=payload.hex(),
        received_payload_matches=bool(completed and rounds[-1]['reply'] and
            rounds[-1]['reply']['payload_hex']==payload.hex()),
        ack_type_decoded=True,data_triggered_reply=True,standard_ack_timing_verified=False,
        ack_timeout_seconds=args.ack_timeout,retry_budget=args.attempts,
        timeout_mode='host_wall_clock' if transport.physical else 'virtual_expiry_on_loss')


def type_demo(args,transport):
    iq = np.concatenate([feedback_burst(status,(args.sequence+status)%256) for status in STATUS])
    captures = transport.exchange(None if args.mode=='background' else iq,2_430_000_000,'types')
    frames = []
    for index,capture in enumerate(captures):
        frames.extend(dict(row,capture_block=index) for row in decode_ack(capture))
    matched = {row['status'] for row in frames if row['sequence']==(args.sequence+row['status'])%256}
    return dict(frames=frames,expected_statuses=list(STATUS),matched_statuses=sorted(matched),
        completed=not frames if args.mode=='background' else len(matched)==4,
        negative_control=args.mode=='background',ack_type_decoded=True,data_triggered_reply=False)


def experiment(args):
    run = new_run('ctc_feedback_'+args.mode.replace('-','_'))
    transport = Transport(args,run)
    transport.state(status='starting')
    try:
        result = loop(args,transport,run) if args.mode.startswith('loop-') else type_demo(args,transport)
        status = 'stopped'
    except BaseException as exc:
        transport.state(status='stopped',error=str(exc))
        write_json(run/'status.json',dict(status='stopped',error=str(exc)))
        raise
    finally:
        transport.state(status='stopped')
    report = dict(result,experiment='ors-feedback',mode='ORS-feedback-'+args.mode,
        physical=transport.physical,transport_events=transport.events,
        status=status,independent_nodes_verified=False,independent_ble_verified=False,
        authenticated_sender=False,
        feedback_frame_duration_us=780,type_encoding='2 differential bits for 4 status messages',
        crc8_profile=dict(polynomial='0x07',init=0,xorout=0,reflected=False),
        limitation='相位参考+序号+CRC8 的自定义 ORS 扩展；单板顺序 RF/电脑控制，非原论文逐行复现，非 192us 实时 ACK')
    write_json(run/'report.json',report)
    print(json.dumps({key:report[key] for key in ('mode','completed','ack_type_decoded','data_triggered_reply')},indent=2))
    print(f'Report: {run/"report.json"}',flush=True)
    return 0 if report['completed'] else 2


def main():
    sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['types-simulate','types-rf','loop-simulate','loop-rf','background'],required=True)
    p.add_argument('--uri',default='usb:1.5.5')
    p.add_argument('--encoding',choices=['utf8','hex'],default='utf8')
    p.add_argument('--data',default='Hello CTC')
    p.add_argument('--sequence',type=int,default=secrets.randbelow(256))
    p.add_argument('--attempts',type=int,default=3)
    p.add_argument('--blocks',type=int,default=2)
    p.add_argument('--ack-timeout',type=float,default=10)
    p.add_argument('--drop-first-ack',action='store_true')
    p.add_argument('--drop-first-data',action='store_true')
    for key,value in [('snr',24),('cfo',12000),('clock',10),('tx-gain',-25),('rx-gain',20)]:
        p.add_argument('--'+key,type=float,default=value)
    args = p.parse_args()
    if not 0<=args.sequence<=255 or not 1<=args.blocks<=6 or not 1<=args.attempts<=5 or not 1<=args.ack_timeout<=30:
        p.error('Invalid sequence/blocks/attempts/timeout')
    if args.mode.endswith('-rf') and (args.drop_first_ack or args.drop_first_data):
        p.error('Loss injection is simulation-only')
    try:
        return experiment(args)
    except (ValueError,RuntimeError,OSError,ImportError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
