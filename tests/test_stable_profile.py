import json
from pathlib import Path

import pytest

from eor_control.stable_profile import (
    ValidationLevel,
    apply_missing_software_settings,
    automatic_pump_port_candidates,
    load_stable_profile,
    software_settings,
    validate_stable_profile,
)

PROFILE_PATH = Path(__file__).parents[1] / "config" / "stable-defaults.json"


def test_stable_default_profile_schema_is_valid_but_hardware_is_blocked() -> None:
    profile = load_stable_profile(PROFILE_PATH)
    validation = validate_stable_profile(profile)

    assert profile.schema_version == 1
    assert validation.application_can_start
    assert not validation.hardware_measurement_can_start
    assert validation.for_key("safety.valve_safe_output_v") is not None
    assert (
        validation.for_key("safety.valve_safe_output_v").level
        is ValidationLevel.REQUIRES_PHYSICAL_VALIDATION
    )


def test_stable_software_timing_has_three_poll_stale_window() -> None:
    profile = load_stable_profile(PROFILE_PATH)
    acquisition = profile.section("acquisition")

    assert acquisition["recording_interval_seconds"] == 1.0
    assert acquisition["hardware_status_poll_interval_seconds"] == 1.0
    assert acquisition["stale_timeout_seconds"] == 3.0
    assert float(acquisition["stale_timeout_seconds"]) >= 3.0 * float(
        acquisition["hardware_status_poll_interval_seconds"]
    )


def test_migration_applies_only_missing_software_values() -> None:
    profile = load_stable_profile(PROFILE_PATH)
    target: dict[str, object] = {"recording/interval_seconds": 2.0}

    applied = apply_missing_software_settings(target, profile)

    assert target["recording/interval_seconds"] == 2.0
    assert target["hardware/stale_timeout_seconds"] == 3.0
    assert "recording/interval_seconds" not in applied
    assert "hardware/stale_timeout_seconds" in applied


def test_obsolete_safe_output_and_pid_validation_flags_are_not_seeded() -> None:
    settings = software_settings(load_stable_profile(PROFILE_PATH))

    assert "hardware/safe_output_validated" not in settings
    assert "pid/profile_validated" not in settings


def test_invalid_stale_window_blocks_application_start(tmp_path: Path) -> None:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    payload["acquisition"]["stale_timeout_seconds"] = 2.0
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="three polling periods"):
        load_stable_profile(path)


def test_com3_is_not_an_automatic_candidate_but_service_mode_can_override() -> None:
    ports = ("COM1", "COM2", "COM3", "COM4", "com2")

    assert automatic_pump_port_candidates(ports) == ("COM1", "COM2", "COM4")
    assert automatic_pump_port_candidates(ports, service_mode=True) == (
        "COM1",
        "COM2",
        "COM3",
        "COM4",
    )
