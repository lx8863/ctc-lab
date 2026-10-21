"""Optional AD936x/libiio adapter, independent of the previous project.

No device is opened on import. Detection is read-only; all TX commands are explicit.
"""
from __future__ import annotations
import os
from pathlib import Path
import numpy as np

_DLL_HANDLES = []


def _native() -> None:
    if os.name != "nt" or _DLL_HANDLES:
        return
    directory = Path(os.environ.get("CTC_IIO_LIB_DIR",
                     str(Path(__file__).resolve().parents[1]/"native")))
    if not (directory/"libiio.dll").is_file():
        raise RuntimeError("Missing native/libiio.dll. Set CTC_IIO_LIB_DIR to a libiio 0.25 directory.")
    os.environ["PATH"] = str(directory)+os.pathsep+os.environ.get("PATH","")
    _DLL_HANDLES.append(os.add_dll_directory(str(directory)))


def devices() -> dict:
    _native()
    import iio
    return dict(iio.scan_contexts())


class Radio:
    def __init__(self, uri: str, sample_rate: int, frequency: int,
                 tx_gain: float = -40, rx_gain: float = 20, channel: int = 0):
        if channel not in (0,1):
            raise ValueError("RF channel must be 0 or 1")
        if not -89.75 <= tx_gain <= 0 or not 0 <= rx_gain <= 73:
            raise ValueError("Unsupported AD936x gain range")
        _native()
        import adi
        self.sdr = adi.ad9361(uri=uri)
        try:
            self.sdr._ctx.set_timeout(5000)
            self.sdr.tx_enabled_channels = [channel]
            self.sdr.rx_enabled_channels = [channel]
            self.sdr.sample_rate = sample_rate
            if int(self.sdr.sample_rate) != sample_rate:
                raise RuntimeError(f"Device sample rate {self.sdr.sample_rate} != requested {sample_rate}")
            self.sdr.tx_lo = frequency
            self.sdr.rx_lo = frequency
            self.sdr.tx_rf_bandwidth = min(sample_rate,4_000_000)
            self.sdr.rx_rf_bandwidth = min(sample_rate,4_000_000)
            self.sdr.loopback = 0  # Physical RF path only.
            setattr(self.sdr,f"tx_hardwaregain_chan{channel}",tx_gain)
            setattr(self.sdr,f"gain_control_mode_chan{channel}","manual")
            setattr(self.sdr,f"rx_hardwaregain_chan{channel}",rx_gain)
            self.sdr.tx_cyclic_buffer = True
            self.settings = dict(uri=uri,sample_rate=int(self.sdr.sample_rate),
                frequency_hz=frequency,channel=channel,tx_gain_db=tx_gain,
                rx_gain_db=rx_gain,loopback=0,
                tx_bandwidth=int(self.sdr.tx_rf_bandwidth),
                rx_bandwidth=int(self.sdr.rx_rf_bandwidth))
        except Exception:
            self.close()
            raise

    def transmit(self, iq: np.ndarray) -> None:
        iq = np.asarray(iq)
        peak = float(np.max(np.abs(iq)))
        if not peak or not np.isfinite(iq).all():
            raise ValueError("TX IQ must be finite and nonzero")
        self.sdr.tx((iq/peak * (2**13)).astype(np.complex64))

    def capture(self, samples: int = 262144, blocks: int = 4) -> tuple[list[np.ndarray],list[dict]]:
        self.sdr.rx_buffer_size = samples
        # Capture blocks may have gaps. Do NOT concatenate across native calls.
        self.sdr.rx()
        captures = []
        stats = []
        for _ in range(blocks):
            iq = self.sdr.rx()
            if isinstance(iq,list):
                iq = iq[0]
            iq = np.asarray(iq,dtype=np.complex64)
            captures.append(iq)
            stats.append(dict(samples=len(iq),rms=float(np.sqrt(np.mean(np.abs(iq)**2))),
                peak_component=float(max(np.max(np.abs(iq.real)),np.max(np.abs(iq.imag)))),
                near_adc_full_scale_fraction=float(np.mean(
                    (np.abs(iq.real)>=2000)|(np.abs(iq.imag)>=2000)))))
        return captures,stats

    def close(self) -> None:
        if getattr(self,"sdr",None) is not None:
            try:
                self.sdr.tx_destroy_buffer()
            finally:
                try:
                    self.sdr.rx_destroy_buffer()
                finally:
                    self.sdr = None

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()
