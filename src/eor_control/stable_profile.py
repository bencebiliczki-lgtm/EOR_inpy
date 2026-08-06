import json
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path
from typing import Any, cast

CURRENT_SCHEMA_VERSION = 1
PROFILE_NAME = "AFKI-EOR Stabil alapbeállítások"


class ValidationLevel(StrEnum):
    READY = "READY"
    WARNING = "WARNING"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    DISCONNECTED = "DISCONNECTED"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    REQUIRES_PHYSICAL_VALIDATION = "REQUIRES_PHYSICAL_VALIDATION"


@dataclass(frozen=True, slots=True)
class ProfileIssue:
    key: str
    level: ValidationLevel
    message: str
    blocks_application: bool = False
    blocks_hardware_measurement: bool = False


@dataclass(frozen=True, slots=True)
class StableProfileValidation:
    issues: tuple[ProfileIssue, ...]

    @property
    def application_can_start(self) -> bool:
        return not any(issue.blocks_application for issue in self.issues)

    @property
    def hardware_measurement_can_start(self) -> bool:
        return self.application_can_start and not any(
            issue.blocks_hardware_measurement for issue in self.issues
        )

    def for_key(self, key: str) -> ProfileIssue | None:
        return next((issue for issue in self.issues if issue.key == key), None)


@dataclass(frozen=True, slots=True)
class StableProfile:
    payload: Mapping[str, object]

    @property
    def schema_version(self) -> int:
        return cast(int, self.payload["schema_version"])

    @property
    def profile_name(self) -> str:
        return cast(str, self.payload["profile_name"])

    def section(self, name: str) -> Mapping[str, object]:
        value = self.payload.get(name)
        return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def load_stable_profile(path: Path) -> StableProfile:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"stable profile cannot be read: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"stable profile is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("stable profile root must be an object")
    migrated = migrate_profile(cast(dict[str, object], payload))
    profile = StableProfile(migrated)
    validation = validate_stable_profile(profile)
    application_errors = tuple(
        issue for issue in validation.issues if issue.blocks_application
    )
    if application_errors:
        raise ValueError(
            "invalid stable profile: "
            + "; ".join(f"{issue.key}: {issue.message}" for issue in application_errors)
        )
    return profile


def migrate_profile(payload: dict[str, object]) -> dict[str, object]:
    version = payload.get("schema_version", 0)
    if version == CURRENT_SCHEMA_VERSION:
        return payload
    if version != 0:
        raise ValueError(f"unsupported stable profile schema version: {version}")
    migrated = dict(payload)
    migrated["schema_version"] = CURRENT_SCHEMA_VERSION
    migrated.setdefault("profile_name", PROFILE_NAME)
    migrated.setdefault("modified_at_utc", datetime.now(UTC).isoformat())
    migrated.setdefault("validation_status", "NOT_PHYSICALLY_VALIDATED")
    return migrated


def validate_stable_profile(profile: StableProfile) -> StableProfileValidation:
    issues: list[ProfileIssue] = []
    if profile.schema_version != CURRENT_SCHEMA_VERSION:
        issues.append(
            ProfileIssue(
                "schema_version",
                ValidationLevel.INVALID_CONFIGURATION,
                "unsupported schema version",
                blocks_application=True,
            )
        )
    if not profile.profile_name.strip():
        issues.append(
            ProfileIssue(
                "profile_name",
                ValidationLevel.INVALID_CONFIGURATION,
                "profile name is empty",
                blocks_application=True,
            )
        )
    try:
        datetime.fromisoformat(cast(str, profile.payload["modified_at_utc"]))
    except (KeyError, TypeError, ValueError):
        issues.append(
            ProfileIssue(
                "modified_at_utc",
                ValidationLevel.INVALID_CONFIGURATION,
                "modification timestamp is missing or invalid",
                blocks_application=True,
            )
        )

    acquisition = profile.section("acquisition")
    numeric_rules = {
        "daq_sample_rate_hz": 10.0,
        "control_update_rate_hz": 5.0,
        "recording_interval_seconds": 1.0,
        "ui_numeric_refresh_rate_hz": 2.0,
        "plot_refresh_rate_hz": 2.0,
        "hardware_status_poll_interval_seconds": 1.0,
        "stale_timeout_seconds": 3.0,
        "serial_command_timeout_seconds": 2.0,
        "serial_command_retries": 2.0,
    }
    for key, expected in numeric_rules.items():
        value = acquisition.get(key)
        if not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
            issues.append(
                ProfileIssue(
                    f"acquisition.{key}",
                    ValidationLevel.INVALID_CONFIGURATION,
                    "must be positive and finite",
                    blocks_application=True,
                )
            )
        elif float(value) != expected:
            issues.append(
                ProfileIssue(
                    f"acquisition.{key}",
                    ValidationLevel.WARNING,
                    f"differs from the validated software baseline ({expected:g})",
                )
            )
    poll = acquisition.get("hardware_status_poll_interval_seconds")
    stale = acquisition.get("stale_timeout_seconds")
    if (
        isinstance(poll, (int, float))
        and isinstance(stale, (int, float))
        and float(stale) < 3.0 * float(poll)
    ):
        issues.append(
            ProfileIssue(
                "acquisition.stale_timeout_seconds",
                ValidationLevel.INVALID_CONFIGURATION,
                "stale timeout must cover at least three polling periods",
                blocks_application=True,
            )
        )

    devices = profile.section("devices")
    pumps = devices.get("pumps")
    pump_map = cast(Mapping[str, object], pumps) if isinstance(pumps, Mapping) else {}
    ports: list[str] = []
    for role in ("jacket", "injection"):
        raw = pump_map.get(role)
        config = cast(Mapping[str, object], raw) if isinstance(raw, Mapping) else {}
        port = config.get("port")
        baud = config.get("baud_rate")
        unit_id = config.get("unit_id")
        if not isinstance(port, str) or not port.strip():
            issues.append(
                ProfileIssue(
                    f"devices.pumps.{role}.port",
                    ValidationLevel.NOT_CONFIGURED,
                    "pump port must be identified by read-only discovery",
                    blocks_hardware_measurement=True,
                )
            )
        else:
            ports.append(port.strip().upper())
            if port.strip().upper() == "COM3":
                issues.append(
                    ProfileIssue(
                        f"devices.pumps.{role}.port",
                        ValidationLevel.REQUIRES_PHYSICAL_VALIDATION,
                        "COM3 is the known Intel AMT/SOL port",
                        blocks_hardware_measurement=True,
                    )
                )
        if baud is None or unit_id is None:
            issues.append(
                ProfileIssue(
                    f"devices.pumps.{role}.serial_identity",
                    ValidationLevel.NOT_CONFIGURED,
                    "baud rate and unit ID require a successful read-only query",
                    blocks_hardware_measurement=True,
                )
            )
    if len(ports) != len(set(ports)):
        issues.append(
            ProfileIssue(
                "devices.pumps",
                ValidationLevel.INVALID_CONFIGURATION,
                "the two pumps cannot share a serial port",
                blocks_hardware_measurement=True,
            )
        )

    ni_device = devices.get("ni_device")
    ni = cast(Mapping[str, object], ni_device) if isinstance(ni_device, Mapping) else {}
    channels_value = ni.get("channels")
    channels = (
        cast(Mapping[str, object], channels_value)
        if isinstance(channels_value, Mapping)
        else {}
    )
    for key in ("device_name", "terminal_configuration"):
        if not ni.get(key):
            issues.append(
                ProfileIssue(
                    f"devices.ni_device.{key}",
                    ValidationLevel.NOT_CONFIGURED,
                    "NI hardware must be selected from read-only discovery",
                    blocks_hardware_measurement=True,
                )
            )
    for key in ("differential_pressure", "line_pressure", "valve_output"):
        if not channels.get(key):
            issues.append(
                ProfileIssue(
                    f"devices.ni_device.channels.{key}",
                    ValidationLevel.NOT_CONFIGURED,
                    "NI channel is not configured",
                    blocks_hardware_measurement=True,
                )
            )

    if profile.section("calibration").get("differential_pressure") is None:
        issues.append(
            ProfileIssue(
                "calibration.differential_pressure",
                ValidationLevel.NOT_CONFIGURED,
                "differential-pressure scale must come from calibration or nameplate",
                blocks_hardware_measurement=True,
            )
        )

    required_physical = {
        "safety.injection_max_pressure_bar": profile.section("safety").get(
            "injection_max_pressure_bar"
        ),
        "safety.jacket_max_pressure_bar": profile.section("safety").get(
            "jacket_max_pressure_bar"
        ),
        "safety.differential_max_pressure_bar": profile.section("safety").get(
            "differential_max_pressure_bar"
        ),
        "safety.pressure_overshoot_shutdown_bar": profile.section("safety").get(
            "pressure_overshoot_shutdown_bar"
        ),
        "safety.valve_safe_output_v": profile.section("safety").get(
            "valve_safe_output_v"
        ),
        "pid.control_source": profile.section("pid").get("control_source"),
        "pid.kp": profile.section("pid").get("kp"),
        "pid.ki": profile.section("pid").get("ki"),
        "pid.kd": profile.section("pid").get("kd"),
    }
    for key, value in required_physical.items():
        if value is None:
            issues.append(
                ProfileIssue(
                    key,
                    ValidationLevel.REQUIRES_PHYSICAL_VALIDATION,
                    "no universal physical default is permitted",
                    blocks_hardware_measurement=True,
                )
            )
    if profile.section("safety").get("valve_direction_validated") is not True:
        issues.append(
            ProfileIssue(
                "safety.valve_direction_validated",
                ValidationLevel.REQUIRES_PHYSICAL_VALIDATION,
                "valve direction has not been physically validated",
                blocks_hardware_measurement=True,
            )
        )
    for key in (
        "injection_start_pressure_bar",
        "jacket_start_pressure_bar",
        "injection_startup_flow_ml_per_hour",
        "injection_measurement_flow_ml_per_hour",
        "control_target_pressure_bar",
    ):
        if profile.section("measurement").get(key) is None:
            issues.append(
                ProfileIssue(
                    f"measurement.{key}",
                    ValidationLevel.NOT_CONFIGURED,
                    "measurement-specific physical value is required",
                    blocks_hardware_measurement=True,
                )
            )
    return StableProfileValidation(tuple(issues))


SOFTWARE_SETTING_MAP: Mapping[str, tuple[str, object]] = {
    "acquisition.recording_interval_seconds": ("recording/interval_seconds", 1.0),
    "acquisition.hardware_status_poll_interval_seconds": (
        "hardware/status_poll_interval_seconds",
        1.0,
    ),
    "acquisition.stale_timeout_seconds": ("hardware/stale_timeout_seconds", 3.0),
    "acquisition.serial_command_timeout_seconds": (
        "hardware/serial_command_timeout_seconds",
        2.0,
    ),
    "acquisition.serial_command_retries": ("hardware/serial_command_retries", 2),
    "ui.live_plot_window_minutes": ("ui/live_plot_window_minutes", 10),
    "ui.maximum_visible_plot_points_per_series": (
        "ui/maximum_visible_plot_points_per_series",
        2000,
    ),
    "storage.local_first": ("storage/local_first", True),
    "storage.flush_interval_seconds": ("storage/flush_interval_seconds", 5.0),
    "storage.nas_sync_during_measurement": (
        "storage/nas_sync_during_measurement",
        False,
    ),
}


def software_settings(profile: StableProfile) -> dict[str, object]:
    result: dict[str, object] = {}
    for dotted_key, (settings_key, fallback) in SOFTWARE_SETTING_MAP.items():
        section_name, field = dotted_key.split(".", 1)
        result[settings_key] = profile.section(section_name).get(field, fallback)
    result["profile/schema_version"] = profile.schema_version
    result["profile/name"] = profile.profile_name
    result["profile/validation_status"] = profile.payload.get(
        "validation_status", "NOT_PHYSICALLY_VALIDATED"
    )
    result.update(
        {
            "hardware/valve_direction_validated": False,
            "hardware/pump_shutdown_validated": False,
            "safety/limits_validated": False,
            "calibration/profile_validated": False,
        }
    )
    return result


def apply_missing_software_settings(
    target: MutableMapping[str, object], profile: StableProfile
) -> tuple[str, ...]:
    applied: list[str] = []
    for key, value in software_settings(profile).items():
        if key not in target:
            target[key] = value
            applied.append(key)
    return tuple(applied)


def automatic_pump_port_candidates(
    ports: tuple[str, ...], *, service_mode: bool = False
) -> tuple[str, ...]:
    """Exclude the known AMT/SOL port unless service mode explicitly opts in."""
    unique = dict.fromkeys(port.strip().upper() for port in ports if port.strip())
    return tuple(
        port for port in unique if service_mode or port != "COM3"
    )


def mutable_settings_snapshot(mapping: Mapping[str, Any]) -> dict[str, object]:
    return {str(key): value for key, value in mapping.items()}
