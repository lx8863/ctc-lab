import numpy as np
import pytest
from ctc_lab.storage import save_iq,load_iq


def test_iq_round_trip_and_metadata_mismatch(tmp_path):
    path = tmp_path/"capture.npy"
    original = np.array([1+2j,-3+4j],dtype=np.complex64)
    save_iq(path,original,8_000_000,source="test")
    iq,metadata = load_iq(path)
    np.testing.assert_array_equal(iq,original)
    assert metadata["sample_rate"] == 8_000_000
    np.save(path,np.array([1j],dtype=np.complex64),allow_pickle=False)
    with pytest.raises(ValueError,match="count"):
        load_iq(path)
