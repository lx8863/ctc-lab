"""Reprocess saved RF with the current fixed receiver and correlate PC evidence."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
from ctc_lab.ctc_experiment import receive,evaluate
from ctc_lab.storage import ROOT,new_run,write_json,load_iq
from ctc_lab.msk_receiver import decode as fixed_decode


def main():
    sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('rf_run',type=Path)
    p.add_argument('--pc-report',type=Path)
    p.add_argument('--standard-control',type=Path)
    p.add_argument('--ble-control',type=Path)
    args = p.parse_args()
    manifest = json.loads((args.rf_run/'tx_manifest.json').read_text(encoding='utf-8'))
    blocks = sorted(args.rf_run.glob('rx_*.npy'))
    if not blocks:
        p.error('No saved RX captures in RF run')
    result = dict(ieee802154_frames=[],ble_primary_frames=[],ble_auxiliary_frames=[])
    for index,path in enumerate(blocks):
        iq,metadata = load_iq(path)
        decoded = receive(iq,metadata['sample_rate'])
        for key,frames in decoded.items():
            result[key].extend(dict(frame,capture_block=index) for frame in frames)
    report = evaluate(result,manifest)
    report.update(source_rf_run=str(args.rf_run.resolve()),mode='saved-RF-current-receiver-revalidation',
        receiver_source_sha256=hashlib.sha256((ROOT/'ctc_lab'/'msk_receiver.py').read_bytes()).hexdigest(),
        mapper_source_sha256=hashlib.sha256((ROOT/'ctc_lab'/'ctc_mapper.py').read_bytes()).hexdigest(),
        commodity_zigbee_confirmed=False,external_ble_confirmed=False,packet_reception_ratio=None)
    if args.pc_report:
        pc = json.loads(args.pc_report.read_text(encoding='utf-8'))
        expected = {m['manufacturer_data_hex'][4:] for m in manifest}
        received = {frame.get('manufacturer_data',{}).get('0xFFFF','') for frame in pc['frames']}
        unexpected = received-expected
        matched = expected&received
        report.update(pc_report=str(args.pc_report.resolve()),
            pc_matching_unique_payloads=len(matched),pc_expected_unique_payloads=len(expected),
            pc_unexpected_unique_payloads=len(unexpected),
            external_ble_confirmed=len(matched)==len(expected) and not unexpected,
            pc_target_callbacks=pc['target_advertisements'])
    controls = []
    for kind,path in (('standard-802.15.4',args.standard_control),('ordinary-BLE',args.ble_control)):
        if path:
            iq,metadata = load_iq(path)
            frames = fixed_decode(iq,metadata['sample_rate'])
            controls.append(dict(input=kind,source=str(path.resolve()),
                valid_target_frames=sum(frame['fcs_ok'] for frame in frames),
                note='Historical RF control at its own center frequency/gain; not a matched performance comparison'))
    report['historical_rf_controls'] = controls
    run = new_run('ctc_rf_revalidation')
    write_json(run/'report.json',report)
    print(json.dumps({k:v for k,v in report.items() if not k.endswith('frames')},indent=2))
    print(f"Report: {run/'report.json'}")
    passed = (report['matched_unique_frames']==report['expected_unique_frames']
              and report['ble_matched_unique_auxiliary_pdus']==report['expected_unique_frames'])
    if args.pc_report:
        passed = passed and report['external_ble_confirmed']
    for control in controls:
        passed = passed and (control['valid_target_frames']>0 if control['input']=='standard-802.15.4'
                             else control['valid_target_frames']==0)
    return 0 if passed else 2


if __name__=='__main__':
    raise SystemExit(main())
