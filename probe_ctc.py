"""Early symbol and packet probe. Raw mapping outputs are NOT BLE packets."""
from __future__ import annotations
import numpy as np
from ctc_lab.ctc_mapper import search_codebook,encode_symbols,encode_frame,capacity_report
from ctc_lab.ble import gfsk
from ctc_lab.ieee802154 import modulate_symbols,build_mac,modulate
from ctc_lab.msk_receiver import aligned_symbols,decode


def main():
    rate = 8_000_000
    book,rows = search_codebook()
    for row in rows:
        print(row)
    # Every predecessor / target / successor combination, all in one waveform.
    target = np.array([(a,b,c) for a in range(16) for b in range(16) for c in range(16)],dtype=np.uint8).reshape(-1)
    for label,wave in (("standard",modulate_symbols(target,rate)),
                       ("BLE-constrained",gfsk(encode_symbols(target),rate))):
        # Ignore the final symbol; diagnostic needs one chip beyond its interval.
        starts = np.arange(len(target)-1)*128
        decisions,scores = aligned_symbols(wave,rate,starts)
        wrong = np.flatnonzero(decisions!=target[:-1])
        print(label,"symbol errors:",len(wrong),"/",len(starts),"examples",wrong[:12])
    frame = build_mac(b"CTC",sequence=13)
    print("capacity",capacity_report(frame))
    for label,wave in (("standard",modulate(frame,rate)),
                       ("raw BLE-constrained region",gfsk(encode_frame(frame)+b"\x55\x55",rate))):
        iq = np.pad(wave,(700,2000))
        result = decode(iq,rate)
        print(label,result)


if __name__=="__main__":
    main()
