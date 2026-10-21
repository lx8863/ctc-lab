"""BLE-to-802.15.4 CTC: symbol QA, full extended-event simulation and RF tests."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
import numpy as np

from ctc_lab.ctc_mapper import search_codebook,encode_symbols
from ctc_lab.ble import gfsk
from ctc_lab.ieee802154 import modulate_symbols
from ctc_lab.msk_receiver import aligned_symbols,DIFFERENTIAL
from ctc_lab.ctc_experiment import cycle,receive,evaluate
from ctc_lab.ble_extended import LO_FREQUENCY
from ctc_lab.channel import impair
from ctc_lab.storage import ROOT,new_run,write_json,save_iq,load_iq


def payloads(seed=20261020):
    rng = np.random.default_rng(seed)
    return [b"CTC"+bytes([index])+rng.bytes(2) for index in range(3)]


def symbol_report(args):
    run = new_run("ctc_symbols")
    _,rows = search_codebook()
    contexts = np.array([(a,b,c) for a in range(16) for b in range(16) for c in range(16)],dtype=np.uint8)
    symbols = contexts.reshape(-1)
    starts = (3*np.arange(len(contexts))+1)*128
    actual = gfsk(encode_symbols(symbols))
    decision,scores = aligned_symbols(actual,8_000_000,starts)
    standard,_ = aligned_symbols(modulate_symbols(symbols,8_000_000),8_000_000,starts)
    errors = int(np.count_nonzero(decision!=contexts[:,1]))
    sorted_scores = np.sort(scores,axis=1)
    report = dict(mode="GFSK-symbol-mapping-simulation",candidate_words_per_symbol=65536,
        target_symbols=16,contexts_tested=4096,errors=errors,
        standard_oqpsk_errors=int(np.count_nonzero(standard!=contexts[:,1])),
        actual_minimum_margin=int(np.min(sorted_scores[:,-1]-sorted_scores[:,-2])),
        receiver_dictionary_sha256=hashlib.sha256(DIFFERENTIAL.tobytes()).hexdigest(),
        codebook=list(rows),passed=errors==0,hardware_validated=False)
    write_json(run/"report.json",report)
    print(json.dumps({k:v for k,v in report.items() if k!="codebook"},indent=2))
    print(f"Report: {run/'report.json'}")
    return 0 if report["passed"] else 2


def simulate(args):
    run = new_run("ctc_simulation")
    iq,manifests = cycle(payloads(args.seed))
    # Trimming long idle intervals is unnecessary for acquisition correctness;
    # save a short consecutive window with three events for a bounded file demo.
    snippets = []
    start = 0
    for manifest in manifests:
        period = round(manifest["interval_seconds"]*8_000_000)
        samples = round(0.003*8_000_000)
        snippets.append(iq[start:start+samples])
        start += period
    waveform = np.concatenate(snippets)
    captured = impair(waveform,8_000_000,snr_db=args.snr,cfo_hz=args.cfo,
                      clock_ppm=args.clock_ppm,delay_samples=173.3,seed=args.seed)
    save_iq(run/"rx.npy",captured,8_000_000,source="synthetic-extended-CTC-events")
    write_json(run/"tx_manifest.json",manifests)
    # No manifests are passed across the receiver boundary.
    report = evaluate(receive(captured),manifests)
    report.update(mode="simulation",hardware_validated=False,
        snr_db=args.snr,cfo_hz=args.cfo,clock_ppm=args.clock_ppm,
        limitation="Idle gaps compressed for simulation; each internal event retains normative timing")
    write_json(run/"report.json",report)
    print(json.dumps({k:v for k,v in report.items() if not k.endswith("frames")},indent=2))
    print(f"CTC {report['matched_unique_frames']}/3; BLE {report['ble_matched_unique_auxiliary_pdus']}/3")
    print(f"Report: {run/'report.json'}")
    return 0 if report["matched_unique_frames"]==3 and report["ble_matched_unique_auxiliary_pdus"]==3 else 2


def decode_file(args):
    data,metadata = load_iq(args.iq)
    report = receive(data,metadata["sample_rate"])
    run = new_run("ctc_decode")
    report["input"] = str(args.iq.resolve())
    write_json(run/"report.json",report)
    good = sum(frame["fcs_ok"] for frame in report["ieee802154_frames"])
    print(f"802.15.4 valid FCS frames: {good}; report: {run/'report.json'}")
    return 0 if good else 2


def rf(args):
    from ctc_lab.radio import Radio
    if not 1<=args.blocks<=6 or not 0<=args.seconds<=300:
        raise ValueError("Use 1..6 capture blocks and 0..300 seconds")
    run = new_run("ctc_rf")
    iq,manifests = cycle(payloads(args.seed))
    save_iq(run/"tx.npy",iq,8_000_000,source="BLE-extended-advertising-CTC",lo_hz=LO_FREQUENCY)
    write_json(run/"tx_manifest.json",manifests)
    status = dict(status="starting",run_directory=str(run),stop_file=str(run/"STOP"),
        mode="extended-BLE-to-802.15.4-CTC",external_ble_confirmed=False,
        commodity_zigbee_confirmed=False,seconds=args.seconds)
    active = ROOT/"artifacts"/"ctc_active.json"
    write_json(active,status)
    captures = []
    stats = []
    began = None
    try:
        with Radio(args.uri,8_000_000,LO_FREQUENCY,args.tx_gain,args.rx_gain,0) as radio:
            # +/-2 MHz frequency plan requires more bandwidth than baseline RF.
            radio.sdr.tx_rf_bandwidth = 6_000_000
            radio.sdr.rx_rf_bandwidth = 6_000_000
            radio.settings.update(tx_bandwidth=int(radio.sdr.tx_rf_bandwidth),
                                  rx_bandwidth=int(radio.sdr.rx_rf_bandwidth))
            radio.transmit(iq)
            began = time.monotonic()
            status.update(status="transmitting",settings=radio.settings)
            write_json(active,status)
            print(f"TRANSMITTING SDR-CTC-TEST, C2:54:43:00:00:02; {run}",flush=True)
            time.sleep(0.2)
            captures,stats = radio.capture(1_048_576,args.blocks)
            # Optional short advertising window for external PC scanner.
            while time.monotonic()-began<args.seconds:
                if (run/"STOP").exists():
                    break
                time.sleep(0.2)
        status["status"] = "stopped"
    except BaseException as exc:
        status.update(status="failed",error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if began is not None:
            status["elapsed_seconds"] = time.monotonic()-began
        write_json(active,status)
        write_json(run/"status.json",status)
    # RF off before decoding. Native captures remain separate; no fake continuity.
    decoded = dict(ieee802154_frames=[],ble_primary_frames=[],ble_auxiliary_frames=[])
    for k,capture in enumerate(captures):
        save_iq(run/f"rx_{k:02}.npy",capture,8_000_000,
                source="physical-RF-CTC",lo_hz=LO_FREQUENCY,settings=radio.settings)
        block_result = receive(capture)
        for key,frames in block_result.items():
            decoded[key].extend(dict(frame,capture_block=k) for frame in frames)
    report = evaluate(decoded,manifests)
    report.update(status,mode="physical-RF-CTC",capture_stats=stats,
        packet_reception_ratio=None,
        limitation="SDR differential receiver only; cyclic TX and disjoint capture, no measured per-TX PRR")
    write_json(run/"report.json",report)
    print(json.dumps({k:v for k,v in report.items() if not k.endswith("frames")},indent=2))
    print(f"CTC unique frames {report['matched_unique_frames']}/3; BLE {report['ble_matched_unique_auxiliary_pdus']}/3")
    print(f"Report: {run/'report.json'}",flush=True)
    return 0 if report["matched_unique_frames"]==3 and report["ble_matched_unique_auxiliary_pdus"]==3 else 2


def main():
    sys.stdout.reconfigure(encoding="utf-8",errors="backslashreplace")
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(required=True)
    commands.add_parser("symbols").set_defaults(func=symbol_report)
    s = commands.add_parser("simulate")
    s.set_defaults(func=simulate)
    s.add_argument("--seed",type=int,default=20261020)
    s.add_argument("--snr",type=float,default=24)
    s.add_argument("--cfo",type=float,default=12000)
    s.add_argument("--clock-ppm",type=float,default=10)
    s = commands.add_parser("decode")
    from pathlib import Path
    s.add_argument("iq",type=Path)
    s.set_defaults(func=decode_file)
    s = commands.add_parser("rf")
    s.set_defaults(func=rf)
    s.add_argument("--uri",required=True)
    s.add_argument("--seed",type=int,default=20261020)
    s.add_argument("--tx-gain",type=float,default=-25)
    s.add_argument("--rx-gain",type=float,default=20)
    s.add_argument("--blocks",type=int,default=4)
    s.add_argument("--seconds",type=float,default=0,
                   help="Optional bounded TX duration for independent BLE scanner")
    args = p.parse_args()
    try:
        return args.func(args)
    except (ValueError,RuntimeError,OSError,ImportError) as exc:
        print(f"ERROR: {exc}",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
