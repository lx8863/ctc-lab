"""CLI stages for BLE generation, RF verification and bounded phone tests."""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import time
from .ble import advertising_data,advertising_pdu,advertising_cycle
from .ble_receiver import decode_advertisements
from .storage import ROOT,new_run,save_iq,write_json


def _prepare(args):
    marker = bytes.fromhex(args.marker)
    pdu = advertising_pdu(advertising_data(args.name,marker),args.address)
    iq,metadata = advertising_cycle(pdu,args.ble_channel,args.sample_rate)
    metadata.update(name=args.name,address=args.address,test_marker_hex=marker.hex(),
        sample_rate=args.sample_rate,manufacturer_id=65535,
        manufacturer_id_note="Experimental unassigned marker, not a company identity")
    return iq,metadata


def generate(args) -> int:
    iq,metadata = _prepare(args)
    run = new_run("ble_generate")
    save_iq(run/"tx.npy",iq,args.sample_rate,source="BLE-LE1M",channel=args.ble_channel)
    write_json(run/"manifest.json",metadata)
    print(json.dumps(metadata,ensure_ascii=False,indent=2))
    print(f"Generated: {run}")
    return 0


def advertise(args) -> int:
    from .radio import Radio
    if not 1 <= args.seconds <= 600:
        raise ValueError("BLE test duration must be 1..600 seconds")
    iq,metadata = _prepare(args)
    run = new_run("ble_advertise")
    save_iq(run/"tx.npy",iq,args.sample_rate,source="BLE-LE1M",channel=args.ble_channel)
    write_json(run/"manifest.json",metadata)
    active_path = ROOT/"artifacts"/"ble_active.json"
    report = dict(mode="BLE-advertising-phone-test",status="starting",manifest=metadata,
        requested_seconds=args.seconds,phone_reception_confirmed=False,
        stop_file=str(run/"STOP"),run_directory=str(run),rf_frames=[],rf_crc_pass=0)
    write_json(active_path,report)
    began = None
    try:
        with Radio(args.uri,args.sample_rate,metadata["frequency_hz"],
                   args.tx_gain,args.rx_gain,args.rf_channel) as radio:
            radio.transmit(iq)
            began = time.monotonic()
            report.update(status="transmitting",settings=radio.settings,
                started_at=datetime.now(timezone(timedelta(hours=8))).isoformat())
            write_json(active_path,report)
            print(f"TRANSMITTING: {args.name}, address {args.address}, marker {args.marker}",flush=True)
            print(f"Channel {args.ble_channel}; automatic stop after {args.seconds}s; artifacts: {run}",flush=True)
            if args.verify_rf:
                captures,stats = radio.capture(samples=1_048_576,blocks=1)
                save_iq(run/"rx_check.npy",captures[0],args.sample_rate,
                        source="physical-rf-BLE",channel=args.ble_channel)
                received = decode_advertisements(captures[0],args.ble_channel,args.sample_rate)
                matching = [f for f in received if f["pdu_hex"]==metadata["pdu_hex"]]
                report.update(rf_frames=received,rf_crc_pass=len(matching),capture_stats=stats)
                write_json(active_path,report)
                print(f"Local RF verification: {len(matching)} matching BLE frames with valid CRC",flush=True)
                if not matching:
                    raise RuntimeError("No matching BLE packet in local RF capture; phone test stopped")
            while time.monotonic()-began < args.seconds:
                if (run/"STOP").exists():
                    report["stop_reason"] = "stop-file"
                    break
                time.sleep(0.2)
            else:
                report["stop_reason"] = "duration-limit"
        report["status"] = "stopped"
    except BaseException as exc:
        report.update(status="failed",error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if began is not None:
            report["elapsed_seconds"] = time.monotonic()-began
        write_json(run/"report.json",report)
        write_json(active_path,report)
    print(f"Transmission stopped. Report: {run/'report.json'}",flush=True)
    return 0


def stop(args) -> int:
    active_path = ROOT/"artifacts"/"ble_active.json"
    if not active_path.is_file():
        raise ValueError("No BLE transmission record exists")
    active = json.loads(active_path.read_text(encoding="utf-8"))
    if active.get("status")!="transmitting":
        print(f"BLE status: {active.get('status')}; no stop needed")
        return 0
    stop_path = Path(active["stop_file"]).resolve()
    if not stop_path.is_relative_to((ROOT/"artifacts").resolve()):
        raise ValueError("Invalid BLE stop-file location")
    stop_path.write_text("stop\n",encoding="utf-8")
    print("BLE stop requested")
    return 0
