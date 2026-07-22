import pytest

from eor_control.signal_filter import AnalogSignalFilter


def signal_filter(
    *, alpha: float = 0.2, reject_spikes: bool = True
) -> AnalogSignalFilter:
    return AnalogSignalFilter(
        alpha=alpha,
        median_enabled=True,
        spike_rejection_enabled=reject_spikes,
        spike_limit_voltage=0.1,
        spike_confirmation_samples=3,
    )


def test_median_rejects_isolated_voltage_spike() -> None:
    result = signal_filter().process([2.01, 2.02, 4.87, 2.00, 2.03])

    assert result.raw_voltage == pytest.approx(2.02)
    assert result.filtered_voltage == pytest.approx(2.02)


def test_ema_smooths_median_voltage() -> None:
    filter_ = signal_filter(alpha=0.2, reject_spikes=False)
    filter_.process([2.0] * 20)

    result = filter_.process([2.5] * 20)

    assert result.raw_voltage == pytest.approx(2.5)
    assert result.filtered_voltage == pytest.approx(2.1)


def test_large_step_is_held_then_accepted_after_confirmation() -> None:
    filter_ = signal_filter(alpha=0.2)
    filter_.process([2.0] * 20)

    first = filter_.process([2.5] * 20)
    second = filter_.process([2.5] * 20)
    third = filter_.process([2.5] * 20)

    assert first.filtered_voltage == pytest.approx(2.0)
    assert second.filtered_voltage == pytest.approx(2.0)
    assert third.filtered_voltage == pytest.approx(2.1)
