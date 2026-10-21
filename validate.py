"""Bounded, repeatable acceptance experiments; no radio access or transmission."""
from __future__ import annotations
import platform
import time
import numpy as np
from ctc_lab.channel import impair
from ctc_lab.ieee802154 import build_mac,modulate
from ctc_lab.receiver import decode
from ctc_lab.storage import new_run,write_json


def main():
    run = new_run("validation")
    rows = []
    rng = np.random.default_rng(20261020)
    started = time.perf_counter()
    cases = [
        (8_000_000,30,0,0,8),
        (8_000_000,20,37_500,20,22),
        (8_000_000,18,-72_000,-20,22),
        (8_000_000,15,93_000,0,32),
        (8_000_000,20,-95_000,0,116),
        (4_000_000,20,37_500,20,22),
    ]
    for rate,snr,cfo,clock,length in cases:
        successes = 0
        total = 5
        max_error = 0.
        for repeat in range(total):
            frame = build_mac(rng.bytes(length),repeat)
            iq = np.pad(modulate(frame,rate),(57,300))
            received = impair(iq,rate,snr_db=snr,cfo_hz=cfo,phase=repeat*1.31,
                delay_samples=31.15+17.23*repeat,clock_ppm=clock,seed=100+repeat)
            good = [f for f in decode(received,rate) if f["fcs_ok"]]
            matching = [f for f in good if f["psdu_hex"]==frame.hex()]
            successes += int(bool(matching))
            if matching:
                max_error = max(max_error,abs(matching[0]["cfo_hz"]-cfo))
        rows.append(dict(sample_rate=rate,snr_db=snr,cfo_hz=cfo,clock_ppm=clock,
            payload_bytes=length,correct=successes,total=total,
            largest_successful_cfo_estimation_error_hz=max_error))
    noise_false_frames = 0
    for seed in range(3):
        noise_rng = np.random.default_rng(seed)
        noise = noise_rng.standard_normal(65536)+1j*noise_rng.standard_normal(65536)
        noise_false_frames += len(decode(noise))
    report = dict(mode="simulation-only",hardware_validated=False,
        python=platform.python_version(),numpy=np.__version__,cases=rows,
        pure_noise_captures=3,false_detections_in_noise=noise_false_frames,
        elapsed_seconds=time.perf_counter()-started)
    report["passed"] = all(row["correct"]==row["total"] for row in rows) and not noise_false_frames
    write_json(run/"report.json",report)
    lines = ["# 802.15.4 第一阶段仿真验收", "",
        "本报告由 validate.py 实际运行生成。未进行射频测量，未验证真实 Zigbee 芯片互操作。", "",
        "接收器输入仅为 IQ 和采样率；发送端载荷仅在解码结束后用于结果核对。", "",
        "| 采样率 MSPS | SNR dB | 频偏 Hz | 时钟偏差 ppm | 载荷字节 | 正确帧/发送帧 |",
        "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['sample_rate']/1e6:g} | {row['snr_db']} | {row['cfo_hz']} | "
            f"{row['clock_ppm']} | {row['payload_bytes']} | {row['correct']}/{row['total']} |")
    lines.extend(["",f"纯噪声：3 次、每次 65536 点，检测出的帧数 {noise_false_frames}。", "",
        "SNR 按有效信号功率计算；信道含固定相位、随机数据、分数延时、加性白噪声及表中偏差。",
        "有限样本结果不能解释为真实空口收包率，也不是全部信道条件下的性能保证。", "",
        f"验收结果：{'通过' if report['passed'] else '存在失败，见 JSON 明细'}。"])
    (run/"报告.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(f"Report: {run}")
    print(f"Passed: {report['passed']}")
    for row in rows:
        print(row)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
