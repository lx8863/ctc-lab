from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
import numpy as np

from .ieee802154 import build_burst,channel_frequency
from .channel import impair
from .receiver import decode
from .storage import new_run,write_json,save_iq,load_iq


def _summarize(frames: list[dict], expected: list[str] | None = None) -> dict:
    good = [f for f in frames if f["fcs_ok"]]
    summary = dict(detected_frames=len(frames),fcs_pass=len(good),
                   fcs_fail=len(frames)-len(good),frames=frames)
    if expected is not None:
        wanted = Counter(expected)
        got = Counter(f["psdu_hex"] for f in good)
        correct = sum((wanted & got).values())
        summary.update(expected_frames=len(expected),correct_frames=correct,
                       delivery_ratio=correct/len(expected) if expected else 0,
                       unexpected_valid_frames=sum((got-wanted).values()))
    return summary


def _payloads(count: int, seed: int) -> list[bytes]:
    if not 1 <= count <= 100:
        raise ValueError("Frame count must be 1..100")
    rng = np.random.default_rng(seed)
    return [b"CTC-LAB:"+i.to_bytes(2,"little")+rng.bytes(12) for i in range(count)]


def simulate(args) -> int:
    run = new_run("simulation")
    burst = build_burst(_payloads(args.frames,args.seed),args.sample_rate)
    captured = impair(burst.iq,args.sample_rate,args.snr,args.cfo,delay_samples=173.25,
                      clock_ppm=args.clock_ppm,seed=args.seed)
    save_iq(run/"rx.npy",captured,args.sample_rate,source="synthetic-channel")
    # This manifest is for post-decoding evaluation only; never passed to decode.
    expected = [frame.hex() for frame in burst.frames]
    write_json(run/"tx_manifest.json",dict(psdus=expected))
    frames = decode(captured,args.sample_rate)
    report = _summarize(frames,expected)
    report.update(mode="simulation",hardware_validated=False,
                  channel=dict(snr_db=args.snr,cfo_hz=args.cfo,clock_ppm=args.clock_ppm,
                               fractional_delay_samples=173.25,seed=args.seed))
    write_json(run/"report.json",report)
    print(json.dumps({k:v for k,v in report.items() if k!="frames"},indent=2))
    print(f"Artifacts: {run}")
    return 0 if report["correct_frames"]==len(expected) else 2


def generate(args) -> int:
    run = new_run("802154_tx")
    burst = build_burst(_payloads(args.frames,args.seed),args.sample_rate)
    save_iq(run/"tx.npy",burst.iq,args.sample_rate,source="standard-802.15.4-oqpsk")
    write_json(run/"tx_manifest.json",dict(psdus=[f.hex() for f in burst.frames]))
    print(f"Generated: {run/'tx.npy'}")
    return 0


def receive_file(args) -> int:
    iq,metadata = load_iq(args.iq)
    frames = decode(iq,metadata["sample_rate"])
    run = new_run("decode")
    summary = _summarize(frames)
    summary["input"] = str(args.iq.resolve())
    write_json(run/"report.json",summary)
    print(json.dumps(summary,indent=2,ensure_ascii=False))
    print(f"Report: {run/'report.json'}")
    return 0 if summary["fcs_pass"] else 2


def list_devices(args) -> int:
    from .radio import devices
    print(json.dumps(devices(),ensure_ascii=False,indent=2))
    return 0


def rf_loopback(args) -> int:
    from .radio import Radio
    if not 1 <= args.blocks <= 20 or not 4096 <= args.samples <= 1_048_576:
        raise ValueError("Need 1..20 blocks and 4096..1048576 samples per block")
    run = new_run("rf_loopback")
    burst = build_burst(_payloads(args.frames,args.seed),args.sample_rate)
    save_iq(run/"tx.npy",burst.iq,args.sample_rate,source="standard-802.15.4-oqpsk")
    expected = [frame.hex() for frame in burst.frames]
    write_json(run/"tx_manifest.json",dict(psdus=expected))
    # Stop RF emission BEFORE expensive Python decoding, even on exceptions.
    with Radio(args.uri,args.sample_rate,channel_frequency(args.channel),
               args.tx_gain,args.rx_gain,args.rf_channel) as radio:
        radio.transmit(burst.iq)
        time.sleep(0.2)
        captures,stats = radio.capture(args.samples,args.blocks)
        settings = radio.settings
    results = []
    for k,iq in enumerate(captures):
        save_iq(run/f"rx_{k:02}.npy",iq,args.sample_rate,source="physical-rf",
                settings=settings)
        results.extend(dict(frame,capture_block=k) for frame in decode(iq,args.sample_rate))
    summary = _summarize(results)
    # Cyclic TX repeats and capture gaps prevent an exact sent-frame denominator.
    received = {f["psdu_hex"] for f in results if f["fcs_ok"]}
    summary.update(mode="physical-rf",settings=settings,capture_stats=stats,
        expected_unique_frames=len(set(expected)),
        matched_unique_frames=len(set(expected)&received),
        unexpected_unique_frames=len(received-set(expected)),
        packet_reception_ratio=None,
        limitation="Cyclic TX / discontinuous capture: no measured per-transmission PRR")
    write_json(run/"report.json",summary)
    print(json.dumps({k:v for k,v in summary.items() if k!="frames"},indent=2))
    print(f"Artifacts: {run}")
    return 0 if summary["matched_unique_frames"]==summary["expected_unique_frames"] else 2


def parser():
    p = argparse.ArgumentParser(description="Independent CTC laboratory: IEEE 802.15.4 and BLE")
    sub = p.add_subparsers(dest="command",required=True)
    for name,func in (("simulate",simulate),("generate",generate),("rf-loopback",rf_loopback)):
        s = sub.add_parser(name)
        s.add_argument("--sample-rate",type=int,default=8_000_000)
        s.add_argument("--frames",type=int,default=3)
        s.add_argument("--seed",type=int,default=42)
        s.set_defaults(func=func)
        if name == "simulate":
            s.add_argument("--snr",type=float,default=20)
            s.add_argument("--cfo",type=float,default=37500)
            s.add_argument("--clock-ppm",type=float,default=20)
        if name == "rf-loopback":
            s.add_argument("--uri",required=True)
            s.add_argument("--channel",type=int,default=15,help="802.15.4 channel 11..26")
            s.add_argument("--rf-channel",type=int,choices=[0,1],default=0)
            s.add_argument("--tx-gain",type=float,default=-40)
            s.add_argument("--rx-gain",type=float,default=20)
            s.add_argument("--samples",type=int,default=262144)
            s.add_argument("--blocks",type=int,default=4)
    s = sub.add_parser("decode")
    s.add_argument("iq",type=Path)
    s.set_defaults(func=receive_file)
    sub.add_parser("devices").set_defaults(func=list_devices)
    from . import ble_commands
    for name,func in (("ble-generate",ble_commands.generate),("ble-advertise",ble_commands.advertise)):
        s = sub.add_parser(name)
        s.set_defaults(func=func)
        s.add_argument("--sample-rate",type=int,default=8_000_000)
        s.add_argument("--ble-channel",type=int,choices=[37,38,39],default=37)
        s.add_argument("--name",default="SDR-BLE-TEST")
        s.add_argument("--address",default="C2:54:43:00:00:01")
        s.add_argument("--marker",default="12345678",help="Hex test bytes")
        if name=="ble-advertise":
            s.add_argument("--uri",required=True)
            s.add_argument("--seconds",type=float,default=180)
            s.add_argument("--tx-gain",type=float,default=-25)
            s.add_argument("--rx-gain",type=float,default=20)
            s.add_argument("--rf-channel",type=int,choices=[0,1],default=0)
            s.add_argument("--verify-rf",action="store_true")
    sub.add_parser("ble-stop").set_defaults(func=ble_commands.stop)
    return p


def main() -> int:
    # Windows consoles may default to GBK; decoded random payloads need UTF-8.
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8",errors="backslashreplace")
    args = parser().parse_args()
    try:
        return args.func(args)
    except (ValueError,RuntimeError,OSError,ImportError) as exc:
        print(f"ERROR: {exc}",file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
