import pytest

from eor_control.calibration import LinearCalibration


def test_one_to_five_volts_maps_to_zero_to_four_hundred_bar() -> None:
    calibration = LinearCalibration(1.0, 5.0, 0.0, 400.0)

    assert calibration.convert(1.0) == pytest.approx(0.0)
    assert calibration.convert(3.0) == pytest.approx(200.0)
    assert calibration.convert(5.0) == pytest.approx(400.0)


def test_out_of_range_voltage_is_rejected() -> None:
    calibration = LinearCalibration(1.0, 5.0, 0.0, 400.0)

    with pytest.raises(ValueError, match=r"0\.9 V.*1–5 V"):
        calibration.convert(0.9)


def test_invalid_calibration_range_is_rejected_at_configuration_time() -> None:
    with pytest.raises(ValueError, match="voltage_max"):
        LinearCalibration(5.0, 1.0, 0.0, 400.0)
