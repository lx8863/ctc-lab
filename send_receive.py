"""User payload experiments; decoding never receives the expected payload."""
from __future__ import annotations
import argparse
import json
import sys
import time
import numpy as np
from ctc_lab import ble
from ctc_lab.ble_receiver import decode_advertisements
from ctc_lab.ctc_experiment import cycle, receive
from ctc_lab.ieee802154 import build_burst, channel_frequency
from ctc_lab.receiver import decode
from ctc_lab.channel import impair
from ctc_lab.storage import ROOT, new_run, save_iq, write_json

LIMITS = {'ble':10, 'ieee802154':116, 'ctc':38, 'esb':32}
# Each 131 ms block exceeds the longest advertising interval plus a packet.
# Separate 32 ms blocks could all miss or bisect the desired event.
RF_CAPTURE_SAMPLES = 1_048_576


def parse_payload(protocol, encoding, value):
    if protocol not in LIMITS:
        raise ValueError('请选择 BLE、IEEE 802.15.4、CTC 或 ESB')
    if encoding == 'hex':
        try:
            payload = bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError('HEX 必须为成对的十六进制数字，可用空格分隔') from exc
    elif encoding == 'utf8':
        payload = value.encode('utf-8')
    else:
        raise ValueError('不支持的数据格式')
    if not 1 <= len(payload) <= LIMITS[protocol]:
        raise ValueError(f'{protocol} 数据需为 1～{LIMITS[protocol]} 字节，当前 {len(payload)} 字节；中文按 UTF-8 字节计数')
    return payload


def prepare(protocol, payload):
    if protocol == 'esb':
        from ctc_lab.esb import burst, FREQUENCY
        iq, manifest = burst(payload)
        return iq, FREQUENCY, manifest
    if protocol == 'ctc':
        iq, manifests = cycle([payload])
        return iq, 2_428_000_000, {'events':manifests}
    if protocol == 'ieee802154':
        burst = build_burst([payload])
        return burst.iq, channel_frequency(15), {'psdu_hex':burst.frames[0].hex(), 'channel':15}
    pdu = ble.advertising_pdu(ble.advertising_data('SDR-BLE-TEST', payload))
    iq, metadata = ble.advertising_cycle(pdu, 37)
    return iq, ble.frequency(37), metadata


def decode_capture(protocol, iq):
    if protocol == 'esb':
        from ctc_lab.esb_receiver import decode as decode_esb
        return {'esb_frames':decode_esb(iq)}
    if protocol == 'ctc':
        return receive(iq)
    if protocol == 'ieee802154':
        return {'ieee802154_frames':decode(iq, 8_000_000)}
    return {'ble_primary_frames':decode_advertisements(iq, 37, 8_000_000)}


def compare(decoded, protocol, payload):
    rows = []
    key = 'esb_frames' if protocol=='esb' else 'ble_primary_frames' if protocol=='ble' else 'ieee802154_frames'
    for frame in decoded.get(key, []):
        received = frame.get('test_marker_hex' if protocol == 'ble' else 'payload_hex', '')
        good = frame.get('crc_ok' if protocol in ('ble','esb') else 'fcs_ok', False)
        frame['application_payload_hex'] = received
        frame['payload_matches_sent'] = bool(good and received == payload.hex())
        rows.append(dict(protocol='ESB 1M' if protocol=='esb' else 'BLE' if protocol=='ble' else 'IEEE 802.15.4',
            capture_block=frame.get('capture_block'), checksum_ok=bool(good),
            received_hex=received, received_text=bytes.fromhex(received).decode('utf-8', errors='replace'),
            matches_sent=frame['payload_matches_sent']))
    return rows


def experiment(args):
    payload = parse_payload(args.protocol, args.encoding, args.data)
    iq, frequency, manifest = prepare(args.protocol, payload)
    run = new_run('ctc_send_'+args.mode)
    save_iq(run/'tx.npy', iq, 8_000_000, lo_hz=frequency, source=args.protocol)
    write_json(run/'tx_manifest.json', manifest)
    captures, stats, settings = [], [], {}
    state = dict(status='starting', run_directory=str(run), stop_file=str(run/'STOP'))
    if args.mode == 'rf':
        from ctc_lab.radio import Radio
        active = ROOT/'artifacts'/'ctc_active.json'
        write_json(active, state)
        try:
            with Radio(args.uri, 8_000_000, frequency, args.tx_gain, args.rx_gain, 0) as radio:
                if args.protocol == 'ctc':
                    radio.sdr.tx_rf_bandwidth = 6_000_000
                    radio.sdr.rx_rf_bandwidth = 6_000_000
                    radio.settings.update(tx_bandwidth=6_000_000, rx_bandwidth=6_000_000)
                settings = radio.settings
                if not (run/'STOP').exists():
                    radio.transmit(iq)
                    state['status'] = 'transmitting'
                    write_json(active, state)
                    print('实际 RF 发射已启动', flush=True)
                    began = time.monotonic()
                    time.sleep(.2)
                    for _ in range(args.blocks):
                        if (run/'STOP').exists():
                            break
                        blocks, block_stats = radio.capture(RF_CAPTURE_SAMPLES, 1)
                        captures.extend(blocks)
                        stats.extend(block_stats)
                    while time.monotonic()-began < args.seconds and not (run/'STOP').exists():
                        time.sleep(.1)
            state['status'] = 'stopped'
        except BaseException as exc:
            state.update(status='failed', error=str(exc))
            raise
        finally:
            write_json(active, state)
            write_json(run/'status.json', state)
        print('RF 已关闭，开始解析采集数据', flush=True)
    else:
        # BLE/CTC retain the first complete event with a small idle guard.
        waveform = iq[:24000] if args.protocol in ('ble', 'ctc', 'esb') else iq
        captures = [impair(waveform, 8_000_000, snr_db=args.snr, cfo_hz=args.cfo,
                           clock_ppm=args.clock, delay_samples=173.3, seed=20261020)]
    decoded = {}
    for index, capture in enumerate(captures):
        save_iq(run/f'rx_{index:02}.npy', capture, 8_000_000, lo_hz=frequency,
                source='physical-RF' if args.mode=='rf' else 'synthetic-channel', settings=settings)
        for key, frames in decode_capture(args.protocol, capture).items():
            decoded.setdefault(key, []).extend(dict(frame, capture_block=index) for frame in frames)
    rows = compare(decoded, args.protocol, payload)
    matched = sum(row['matches_sent'] for row in rows)
    report = dict(decoded, mode='user-data-'+args.mode, protocol=args.protocol,
        hardware_validated=args.mode=='rf', sent_hex=payload.hex(), sent_text=payload.decode('utf-8',errors='replace'),
        sent_bytes=len(payload), decoded_payloads=rows, matched_payload_frames=matched,
        payload_received=matched>0, settings=settings, capture_stats=stats,
        channel_parameters=(dict(snr_db=args.snr, cfo_hz=args.cfo, clock_ppm=args.clock)
                            if args.mode=='simulate' else None),
        capture_block_samples=RF_CAPTURE_SAMPLES if args.mode=='rf' else None,
        commodity_zigbee_confirmed=False, independent_phone_confirmed=False,
        external_nrf24_confirmed=False,
        limitation='本板软件接收解析；外部手机/Zigbee/nRF24 芯片接收需另行验证。循环发射、分块采集，不计算 PRR。')
    write_json(run/'report.json', report)
    print(json.dumps(dict(sent_hex=payload.hex(), decoded_payloads=rows, payload_received=matched>0), ensure_ascii=False, indent=2))
    print(f'Report: {run/"report.json"}', flush=True)
    return 0 if matched else 2


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', choices=LIMITS, required=True)
    parser.add_argument('--encoding', choices=['utf8','hex'], required=True)
    parser.add_argument('--data', required=True)
    parser.add_argument('--mode', choices=['simulate','rf'], required=True)
    parser.add_argument('--uri', default='usb:1.5.5')
    parser.add_argument('--snr', type=float, default=24)
    parser.add_argument('--cfo', type=float, default=12000)
    parser.add_argument('--clock', type=float, default=10)
    parser.add_argument('--tx-gain', type=float, default=-25)
    parser.add_argument('--rx-gain', type=float, default=20)
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--blocks', type=int, default=4)
    args = parser.parse_args()
    if not 0<=args.seconds<=60 or not 1<=args.blocks<=6:
        parser.error('RF 时长需为 0～60 秒，采集块数 1～6')
    try:
        return experiment(args)
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
