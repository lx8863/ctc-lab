"""Reproducible full-event sensitivity trials and negative controls; no RF TX."""
from __future__ import annotations
import hashlib
import sys
import numpy as np
from ctc_lab.ble_extended import event
from ctc_lab.ieee802154 import build_mac,modulate
from ctc_lab.ctc_experiment import receive
from ctc_lab.channel import impair
from ctc_lab.msk_receiver import decode
from ctc_lab.ble import advertising_pdu,advertising_data,wire_packet,gfsk
from ctc_lab.storage import ROOT,new_run,write_json


def main():
    sys.stdout.reconfigure(encoding='utf-8',errors='backslashreplace')
    run = new_run("ctc_validation")
    rng = np.random.default_rng(64121)
    cases = [(30,0,0,6),(24,12000,20,6),(24,-12000,-20,6),
             (20,0,20,12),(20,30000,20,12),(15,0,0,6),
             (24,12000,20,38)]
    rows = []
    for case,(snr,cfo,clock,size) in enumerate(cases):
        matches = 0
        ble_matches = 0
        total = 10
        for repeat in range(total):
            frame = build_mac(rng.bytes(size),sequence=repeat)
            iq,manifest = event(frame,did=case*total+repeat)
            captured = impair(np.pad(iq,(300,1000)),8_000_000,snr_db=snr,
                cfo_hz=cfo,clock_ppm=clock,phase=repeat*1.315,
                delay_samples=17.13+repeat*23.17,seed=case*100+repeat)
            decoded = receive(captured)
            matches += int(any(f["fcs_ok"] and f["psdu_hex"]==frame.hex()
                               for f in decoded["ieee802154_frames"]))
            ble_matches += int(any(f["pdu_hex"]==manifest["auxiliary_pdu_hex"]
                                   for f in decoded["ble_auxiliary_frames"]))
        row = dict(snr_db=snr,cfo_hz=cfo,clock_ppm=clock,payload_bytes=size,
                   correct_target_frames=matches,correct_ble_auxiliary_pdus=ble_matches,total=total)
        print(row,flush=True)
        rows.append(row)
    controls = []
    standard_frame = build_mac(b"same fixed differential receiver",sequence=7)
    standard = impair(np.pad(modulate(standard_frame),(500,2000)),8_000_000,
                      snr_db=24,cfo_hz=12000)
    good = [f for f in decode(standard) if f["fcs_ok"]]
    controls.append(dict(input="standard-802.15.4",valid_frames=len(good),
                         expected_match=any(f["psdu_hex"]==standard_frame.hex() for f in good)))
    ordinary = np.pad(gfsk(wire_packet(advertising_pdu(advertising_data()),39)),(700,2000))
    controls.append(dict(input="ordinary-BLE-no-CTC-mapping",
                         valid_frames=sum(f["fcs_ok"] for f in decode(ordinary))))
    bad_frame = bytearray(build_mac(b"CTC bad FCS"))
    bad_frame[-1] ^= 0x80
    bad_event,_ = event(bytes(bad_frame))
    bad_decoded = receive(np.pad(bad_event,(500,1000)))
    controls.append(dict(input="CTC-correct-BLE-CRC-but-bad-target-FCS",
        valid_frames=sum(f["fcs_ok"] for f in bad_decoded["ieee802154_frames"]),
        valid_ble_pdus=len(bad_decoded["ble_auxiliary_frames"])))
    for seed in range(3):
        gen = np.random.default_rng(seed)
        noise = gen.standard_normal(65536)+1j*gen.standard_normal(65536)
        controls.append(dict(input=f"pure-noise-{seed}",
            valid_frames=sum(f["fcs_ok"] for f in decode(noise))))
    report = dict(mode="simulation-sensitivity",rows=rows,controls=controls,
        hardware_validated=False,
        receiver_source_sha256=hashlib.sha256((ROOT/'ctc_lab'/'msk_receiver.py').read_bytes()).hexdigest(),
        mapper_source_sha256=hashlib.sha256((ROOT/'ctc_lab'/'ctc_mapper.py').read_bytes()).hexdigest(),
        note="Sensitivity observations, not a universal pass criterion or real RF PRR")
    write_json(run/"report.json",report)
    lines = ["# CTC 扰动与对照实验", "", "仅仿真；每行 10 个不同随机目标载荷，完整 BLE 扩展事件。", "",
        "| SNR dB | CFO Hz | 时钟 ppm | 目标载荷字节 | 正确目标帧 | 正确 BLE 辅助包 |",
        "|---|---|---|---|---|---|"]
    lines.extend(f"| {r['snr_db']} | {r['cfo_hz']} | {r['clock_ppm']} | {r['payload_bytes']} | "
        f"{r['correct_target_frames']}/{r['total']} | {r['correct_ble_auxiliary_pdus']}/{r['total']} |" for r in rows)
    lines += ["", "接收器不读取映射表、发送序号列表、载荷或边界；发送清单仅在解码之后评估。", "",
        "SNR 为有效波形功率相对白噪声；仅固定相位、分数延时、频偏与时钟误差，未建模复杂多径。", "",
        "对照结果：", ""]
    lines.extend(f"- {row}" for row in controls)
    (run/'报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f"Report: {run}")
    # Acceptance gate is reference conditions + controls. Stress rows remain
    # disclosed even when they fail, never silently removed or relabelled.
    reference_ok = all(r['correct_target_frames']==r['total'] and
                       r['correct_ble_auxiliary_pdus']==r['total'] for r in rows[:3])
    controls_ok = controls[0]['expected_match'] and all(r['valid_frames']==0 for r in controls[1:])
    return 0 if reference_ok and controls_ok else 2


if __name__=='__main__':
    raise SystemExit(main())
