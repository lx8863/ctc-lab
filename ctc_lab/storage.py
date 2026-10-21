"""Self-describing IQ artifacts. Receiver reads only IQ and public RF metadata."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def new_run(label: str) -> Path:
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_%f")
    path = ROOT / "artifacts" / f"{stamp}_{label}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def save_iq(path: Path, iq: np.ndarray, sample_rate: int, **metadata) -> None:
    # Explicit complex64 NPY representation avoids byte-order / I-Q ambiguity.
    np.save(path,np.asarray(iq,dtype=np.complex64),allow_pickle=False)
    write_json(path.with_suffix(".json"),dict(format="numpy-complex64-I-plus-jQ",
               sample_rate=sample_rate,samples=len(iq),**metadata))


def load_iq(path: Path) -> tuple[np.ndarray,dict]:
    metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    data = np.load(path,allow_pickle=False)
    if data.ndim != 1 or not np.iscomplexobj(data):
        raise ValueError("Expected a one-dimensional complex IQ NPY file")
    if not isinstance(metadata.get("sample_rate"),int) or metadata["sample_rate"] <= 0:
        raise ValueError("IQ metadata must specify a positive integer sample_rate")
    if metadata.get("samples") != len(data):
        raise ValueError("IQ sample count disagrees with metadata")
    return data,metadata
