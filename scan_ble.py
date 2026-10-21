"""Read advertisements through the PC's independent BLE adapter; no connections."""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
import time
from ctc_lab.storage import new_run,write_json


async def scan(args):
    from bleak import BleakScanner
    seen_others = set()
    matches = []
    began = time.monotonic()

    def callback(device,advertisement):
        if device.address.upper()==args.address.upper() or advertisement.local_name==args.name:
            matches.append(dict(elapsed_seconds=time.monotonic()-began,address=device.address,
                name=advertisement.local_name,rssi_dbm=advertisement.rssi,
                manufacturer_data={f"0x{k:04X}":v.hex() for k,v in advertisement.manufacturer_data.items()}))
        else:
            seen_others.add(device.address)

    async with BleakScanner(detection_callback=callback,scanning_mode="active"):
        await asyncio.sleep(args.seconds)
    report = dict(receiver="independent-PC-Bluetooth-adapter",duration_seconds=args.seconds,
        expected_address=args.address,expected_name=args.name,
        other_devices_count=len(seen_others),target_advertisements=len(matches),frames=matches)
    run = new_run("pc_ble_scan")
    write_json(run/"report.json",report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print(f"Report: {run/'report.json'}")
    return 0 if matches else 2


def main():
    sys.stdout.reconfigure(encoding="utf-8",errors="backslashreplace")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds",type=float,default=20)
    p.add_argument("--address",default="C2:54:43:00:00:01")
    p.add_argument("--name",default="SDR-BLE-TEST")
    args = p.parse_args()
    if not 1<=args.seconds<=60:
        p.error("--seconds must be 1..60")
    return asyncio.run(scan(args))


if __name__=="__main__":
    raise SystemExit(main())
