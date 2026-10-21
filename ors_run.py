"""Isolated ORS ACK presence experiment. No data reply or ACK-type inference."""
from __future__ import annotations
import argparse
import json
import sys
import time
from ctc_lab.ors import burst,FREQUENCY
from ctc_lab.ors_receiver import detect
from ctc_lab.channel import impair
from ctc_lab.storage import ROOT,new_run,write_json,save_iq


def experiment(args):
    iq,manifest = burst()
    run = new_run('ctc_ors_'+args.mode)
    save_iq(run/'tx.npy',iq,8_000_000,source='OQPSK-ORS-ACK',lo_hz=FREQUENCY)
    write_json(run/'tx_manifest.json',manifest)
    captures,stats,settings = [],[],{}
    state = dict(status='starting',run_directory=str(run),stop_file=str(run/'STOP'),experiment='ors-ack-presence')
    if args.mode in ('rf','background'):
        from ctc_lab.radio import Radio
        active = ROOT/'artifacts'/'ctc_active.json'
        write_json(active,state)
        try:
            with Radio(args.uri,8_000_000,FREQUENCY,args.tx_gain,args.rx_gain,0) as radio:
                settings = radio.settings
                if not (run/'STOP').exists():
                    if args.mode=='rf':
                        radio.transmit(iq)
                    began = time.monotonic()
                    state['status'] = 'transmitting' if args.mode=='rf' else 'capturing'
                    write_json(active,state)
                    print('ORS ACK 物理波形发射中；只检测存在性' if args.mode=='rf' else
                          '背景对照：仅接收，不创建发射缓冲区',flush=True)
                    for _ in range(args.blocks):
                        if (run/'STOP').exists():
                            break
                        blocks,block_stats = radio.capture(262144,1)
                        captures.extend(blocks)
                        stats.extend(block_stats)
                    while args.mode=='rf' and time.monotonic()-began<args.seconds and not (run/'STOP').exists():
                        time.sleep(.1)
            state['status'] = 'stopped'
        except BaseException as exc:
            state.update(status='failed',error=str(exc))
            raise
        finally:
            write_json(active,state)
            write_json(run/'status.json',state)
        print('RF 已关闭，开始盲检测保存的 IQ',flush=True)
    else:
        captures = [impair(iq,8_000_000,snr_db=args.snr,cfo_hz=args.cfo,
                           clock_ppm=args.clock,delay_samples=173.3,seed=20261020)]
    detections,diagnostics = [],[]
    for index,capture in enumerate(captures):
        save_iq(run/f'rx_{index:02}.npy',capture,8_000_000,lo_hz=FREQUENCY,
                source='physical-RF-ORS' if args.mode in ('rf','background') else 'synthetic-ORS',settings=settings)
        # No manifest, start, schedule, sent type or payload crosses this boundary.
        decoded = detect(capture)
        detections.extend(dict(row,capture_block=index) for row in decoded['detections'])
        diagnostics.append(dict(capture_block=index,noise_power=decoded['noise_power'],settings=decoded['settings']))
    report = dict(experiment='ors-ack-presence',mode='ORS-'+args.mode,
        ack_present=bool(detections),detection_count=len(detections),detections=detections,
        diagnostics=diagnostics,capture_stats=stats,settings=settings,
        channel_parameters=dict(snr_db=args.snr,cfo_hz=args.cfo,clock_ppm=args.clock) if args.mode=='simulate' else None,
        negative_control=args.mode=='background',tx_enabled=args.mode=='rf',
        captured_samples=sum(len(x) for x in captures),sample_rate=8_000_000,
        ack_type_decoded=False,data_triggered_reply=False,standard_ack_timing_verified=False,
        independent_ble_receiver_confirmed=False,packet_reception_ratio=None,
        limitation='ORS 波形存在性实验；没有载荷/CRC/ACK 类型判决，不是实时数据回复闭环或完整论文复现')
    write_json(run/'report.json',report)
    print(json.dumps(dict(ack_present=bool(detections),detection_count=len(detections)),indent=2),flush=True)
    print(f'Report: {run/"report.json"}',flush=True)
    return (0 if not detections else 2) if args.mode=='background' else (0 if detections else 2)


def main():
    sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['simulate','rf','background'],required=True)
    p.add_argument('--uri',default='usb:1.5.5')
    for key,default in [('snr',24),('cfo',12000),('clock',10),('tx-gain',-25),('rx-gain',20),('seconds',1)]:
        p.add_argument('--'+key,type=float,default=default)
    p.add_argument('--blocks',type=int,default=4)
    args = p.parse_args()
    if not 0<=args.seconds<=60 or not 1<=args.blocks<=6:
        p.error('时长需为 0～60 秒、块数 1～6')
    try:
        return experiment(args)
    except (ValueError,RuntimeError,OSError,ImportError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
