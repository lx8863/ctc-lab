import numpy as np
import pytest
from ctc_lab.ieee802154 import build_mac,modulate,modulate_symbols
from ctc_lab.ble import gfsk,whiten,advertising_data,advertising_pdu,wire_packet
from ctc_lab.ctc_mapper import search_codebook,encode_symbols,encode_frame,capacity_report
from ctc_lab.msk_receiver import DIFFERENTIAL,aligned_symbols,decode
from ctc_lab.ble_extended import event,auxiliary_pdu,primary_pdu,parse_pdu,packet
from ctc_lab.ctc_experiment import receive
from ctc_lab.channel import impair


def test_differential_dictionary_is_analytical_standard_oqpsk():
    symbols = np.tile(np.arange(16,dtype=np.uint8),2)
    wave = modulate_symbols(symbols,8_000_000)
    got,scores = aligned_symbols(wave,8_000_000,np.arange(len(symbols)-1)*128)
    np.testing.assert_array_equal(got,symbols[:-1])
    assert np.min(np.max(scores,axis=1))==31


def test_all_4096_predecessor_target_successor_contexts():
    contexts = np.array([(a,b,c) for a in range(16) for b in range(16) for c in range(16)],dtype=np.uint8)
    waveform = gfsk(encode_symbols(contexts.reshape(-1)))
    starts = (3*np.arange(len(contexts))+1)*128
    got,scores = aligned_symbols(waveform,8_000_000,starts)
    np.testing.assert_array_equal(got,contexts[:,1])
    sorted_scores = np.sort(scores,axis=1)
    assert np.min(sorted_scores[:,-1]-sorted_scores[:,-2])>=12
    _,book = search_codebook()
    assert len(book)==16
    assert all(row["ideal_margin"]>=14 for row in book)


def test_capacities_reject_false_legacy_claim():
    frame = build_mac(b"CTC")
    report = capacity_report(frame)
    assert report["required_ble_on_air_bytes"]==80
    assert report["fits_legacy_advdata"] is False
    maximum,_ = auxiliary_pdu(build_mac(bytes(38)))
    assert maximum[1]==253
    with pytest.raises(ValueError,match="capacity"):
        auxiliary_pdu(build_mac(bytes(39)))
    with pytest.raises(ValueError):
        auxiliary_pdu(build_mac(bytes(80)))


def test_extended_structure_pointer_timing_and_inverse_whitening():
    frame = build_mac(b"CTC")
    waveform,metadata = event(frame)
    primary = parse_pdu(primary_pdu())
    aux,manifest = auxiliary_pdu(frame)
    secondary = parse_pdu(aux)
    assert primary["aux_channel"]==12
    assert primary["aux_offset_us"]==600
    assert primary["aux_phy"]==0
    assert primary["adi_hex"]==secondary["adi_hex"]
    assert secondary["address"]=="C2:54:43:00:00:02"
    assert secondary["name"]=="SDR-CTC-TEST"
    assert metadata["actual_aux_frame_space_us"]>=300
    assert 600<=metadata["auxiliary_start_us"]<630
    offset = manifest["ctc_on_air_byte_offset"]
    on_air = packet(aux,12)
    assert on_air[offset:offset+80]==encode_frame(frame)


@pytest.mark.parametrize("snr,cfo,clock",[(30,0,0),(24,12000,0),(24,-12000,20)])
def test_blind_ctc_frame_and_independent_ble_parsing(snr,cfo,clock):
    frame = build_mac(b"CTC",sequence=13)
    x,_ = event(frame)
    captured = impair(np.pad(x,(400,2000)),8_000_000,snr_db=snr,cfo_hz=cfo,
                      delay_samples=71.3,clock_ppm=clock)
    result = receive(captured)
    good = [f for f in result["ieee802154_frames"] if f["fcs_ok"]]
    assert any(f["psdu_hex"]==frame.hex() for f in good), result
    assert result["ble_primary_frames"]
    assert result["ble_auxiliary_frames"]


def test_fixed_receiver_accepts_standard_and_rejects_bad_ctc_fcs():
    frame = build_mac(b"unchanged receiver",sequence=17)
    received = decode(impair(np.pad(modulate(frame),(600,2000)),8_000_000,
                            snr_db=22,cfo_hz=30000))
    assert any(f["psdu_hex"]==frame.hex() and f["fcs_ok"] for f in received)
    bad = bytearray(build_mac(b"CTC"))
    bad[-1] ^= 1
    wave = np.pad(gfsk(encode_frame(bytes(bad))+b"\x55\x55"),(700,2000))
    got = decode(wave)
    assert got
    assert not any(f["fcs_ok"] for f in got)


def test_noise_and_ordinary_ble_do_not_make_ctc_frames():
    rng = np.random.default_rng(7221)
    noise = rng.standard_normal(40000)+1j*rng.standard_normal(40000)
    assert not any(f["fcs_ok"] for f in decode(noise))
    ordinary = np.pad(gfsk(wire_packet(advertising_pdu(advertising_data()),39)),(700,2000))
    assert not any(f["fcs_ok"] for f in decode(ordinary))
