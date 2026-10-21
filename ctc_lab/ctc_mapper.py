"""BLE-constrained symbol search. The receiver's dictionary is never optimized.

Each 16-us 802.15.4 symbol gets 16 BLE LE 1M bits. Search all 65536 words
against the ideal sign discriminator, maximize nearest-competitor margin, then
prefer fewer chip errors and fewer transitions. Actual GFSK is validated later.
"""
from __future__ import annotations
from functools import lru_cache
import numpy as np
from .msk_receiver import DIFFERENTIAL
from .ieee802154 import octets_to_symbols,ppdu


@lru_cache(maxsize=1)
def search_codebook() -> tuple[np.ndarray,tuple[dict,...]]:
    words = np.arange(65536,dtype=np.uint32)
    bits = ((words[:,None]>>np.arange(16))&1).astype(np.int16)
    # Samples at chip peaks: erase the boundary-dependent first increment.
    phase_signs = np.repeat(bits*2-1,2,axis=1)[:,1:]
    scores = phase_signs @ DIFFERENTIAL.T
    transitions = np.count_nonzero(np.diff(bits,axis=1),axis=1)
    selected = []
    rows = []
    for target in range(16):
        rivals = np.max(np.delete(scores,target,axis=1),axis=1)
        margin = scores[:,target]-rivals
        objective = margin.astype(np.int64)*1000+scores[:,target]*20-transitions
        choice = int(np.argmax(objective))
        selected.append(int(words[choice]))
        rows.append(dict(symbol=target,ble_word_hex=f"{words[choice]:04x}",
            ble_bits="".join(map(str,bits[choice].tolist())),
            ideal_internal_chip_errors=int((31-scores[choice,target])//2),
            ideal_score=int(scores[choice,target]),ideal_margin=int(margin[choice]),
            bit_transitions=int(transitions[choice])))
    book = np.array(selected,dtype=np.uint16)
    book.setflags(write=False)
    return book,tuple(rows)


def encode_symbols(symbols: np.ndarray) -> bytes:
    symbols = np.asarray(symbols)
    if symbols.ndim!=1 or np.any(symbols<0) or np.any(symbols>15):
        raise ValueError("Expected symbols in 0..15")
    words,_ = search_codebook()
    return words[symbols.astype(np.uint8)].astype("<u2").tobytes()


def encode_frame(psdu: bytes) -> bytes:
    # This returns desired ON-AIR bits for a BLE payload region, not a BLE PDU.
    return encode_symbols(octets_to_symbols(ppdu(psdu)))


def capacity_report(psdu: bytes) -> dict:
    size = len(encode_frame(psdu))
    return dict(ieee802154_psdu_bytes=len(psdu),
        ieee802154_ppdu_bytes=len(psdu)+6,required_ble_on_air_bytes=size,
        legacy_advertising_data_max_bytes=31,
        fits_legacy_advdata=size<=31,
        reason="250 kb/s target to 1 Mb/s source requires 4 source bytes per target byte")
