import csv
import os
import shutil
from pathlib import Path
from typing import Protocol, TextIO

from eor_control.domain import MeasurementRecord


class MeasurementWriter(Protocol):
    def write(self, record: MeasurementRecord) -> None: ...

    def close(self) -> None: ...


class CsvMeasurementWriter:
    """Append-only raw writer that flushes every complete measurement row."""

    LEGACY_HEADER = (
        "recorded_at_utc",
        "monotonic_seconds",
        "jacket_pressure_bar",
        "jacket_flow_ml_per_hour",
        "jacket_remaining_volume_ml",
        "injection_pressure_bar",
        "injection_flow_ml_per_hour",
        "injection_remaining_volume_ml",
        "injected_volume_ml",
        "line_pressure_bar",
        "differential_pressure_bar",
        "valve_percent",
        "active_stage",
        "quality",
        "safety_reasons",
    )
    V2_HEADER = (
        "recorded_at_utc",
        "monotonic_seconds",
        "jacket_pressure_bar",
        "jacket_flow_ml_per_hour",
        "jacket_remaining_volume_ml",
        "jacket_net_volume_ml",
        "injection_pressure_bar",
        "injection_flow_ml_per_hour",
        "injection_remaining_volume_ml",
        "injection_net_volume_ml",
        "line_pressure_bar",
        "differential_pressure_bar",
        "valve_percent",
        "active_stage",
        "quality",
        "safety_reasons",
    )
    V3_HEADER = (
        "recorded_at_utc",
        "monotonic_seconds",
        "jacket_pressure_bar",
        "jacket_flow_ml_per_hour",
        "jacket_remaining_volume_ml",
        "jacket_net_volume_ml",
        "injection_pressure_bar",
        "injection_flow_ml_per_hour",
        "injection_remaining_volume_ml",
        "injection_net_volume_ml",
        "raw_line_pressure_bar",
        "line_pressure_bar",
        "raw_differential_pressure_bar",
        "differential_pressure_bar",
        "valve_percent",
        "active_stage",
        "quality",
        "safety_reasons",
    )
    V4_HEADER = (
        *V3_HEADER[:10],
        "raw_line_voltage",
        *V3_HEADER[10:12],
        "raw_differential_voltage",
        *V3_HEADER[12:],
    )
    V5_HEADER = (
        *V4_HEADER[:11],
        "median_line_voltage",
        "filtered_line_voltage",
        *V4_HEADER[11:13],
        "line_pressure_quality",
        "line_pressure_quality_reason",
        "line_pressure_sample_age_seconds",
        *V4_HEADER[13:],
    )
    HEADER = (
        *V5_HEADER[:19],
        "median_differential_voltage",
        "filtered_differential_voltage",
        *V5_HEADER[19:21],
        "differential_pressure_quality",
        "differential_pressure_quality_reason",
        "differential_pressure_sample_age_seconds",
        *V5_HEADER[21:],
    )

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        is_empty = not path.exists() or path.stat().st_size == 0
        if not is_empty:
            self._upgrade_legacy_file(path)
        self._file: TextIO = path.open("a", encoding="utf-8", newline="")
        self._writer = csv.writer(self._file, delimiter=";", lineterminator="\n")
        if is_empty:
            self._writer.writerow(self.HEADER)
            self._sync()

    @classmethod
    def _upgrade_legacy_file(cls, path: Path) -> None:
        with path.open(encoding="utf-8", newline="") as file:
            first_line = file.readline()
            file.seek(0)
            delimiter = ";" if ";" in first_line else ","
            rows = list(csv.reader(file, delimiter=delimiter))
        if not rows:
            return
        header = tuple(rows[0])
        if header == cls.HEADER:
            return
        legacy_inlet_column = "inlet_pressure_bar"
        if (
            legacy_inlet_column in header
            and tuple(name for name in header if name != legacy_inlet_column)
            == cls.LEGACY_HEADER
        ):
            inlet_index = header.index(legacy_inlet_column)
            rows = [
                [value for index, value in enumerate(row) if index != inlet_index]
                for row in rows
            ]
            header = tuple(rows[0])
        if header not in (
            cls.LEGACY_HEADER,
            cls.V2_HEADER,
            cls.V3_HEADER,
            cls.V4_HEADER,
            cls.V5_HEADER,
        ):
            raise ValueError("a meglévő mérési CSV fejléce nem támogatott")
        backup_version = (
            "v1"
            if header == cls.LEGACY_HEADER
            else "v2"
            if header == cls.V2_HEADER
            else "v3"
            if header == cls.V3_HEADER
            else "v4"
            if header == cls.V4_HEADER
            else "v5"
        )
        legacy_index = {name: index for index, name in enumerate(header)}
        converted = [list(cls.HEADER)]
        for row in rows[1:]:
            if len(row) != len(header):
                continue
            converted.append(
                [
                    (
                        ""
                        if name == "jacket_net_volume_ml"
                        and name not in legacy_index
                        else row[legacy_index["injected_volume_ml"]]
                        if name == "injection_net_volume_ml"
                        and name not in legacy_index
                        else row[legacy_index["line_pressure_bar"]]
                        if name == "raw_line_pressure_bar"
                        else row[legacy_index["differential_pressure_bar"]]
                        if name == "raw_differential_pressure_bar"
                        else row[legacy_index["raw_line_voltage"]]
                        if name == "median_line_voltage"
                        and "raw_line_voltage" in legacy_index
                        else row[legacy_index["quality"]]
                        if name == "line_pressure_quality"
                        else row[legacy_index["raw_differential_voltage"]]
                        if name == "median_differential_voltage"
                        and "raw_differential_voltage" in legacy_index
                        else row[legacy_index["quality"]]
                        if name == "differential_pressure_quality"
                        else ""
                        if name
                        in {
                            "raw_line_voltage",
                            "median_line_voltage",
                            "filtered_line_voltage",
                            "line_pressure_quality_reason",
                            "line_pressure_sample_age_seconds",
                            "raw_differential_voltage",
                            "median_differential_voltage",
                            "filtered_differential_voltage",
                            "differential_pressure_quality_reason",
                            "differential_pressure_sample_age_seconds",
                        }
                        else row[legacy_index[name]]
                    )
                    for name in cls.HEADER
                ]
            )
        backup = path.with_name(
            f"{path.stem}_{backup_version}_backup{path.suffix}"
        )
        if not backup.exists():
            shutil.copy2(path, backup)
        temporary = path.with_suffix(f"{path.suffix}.upgrade.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file, delimiter=";", lineterminator="\n")
            writer.writerows(converted)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)

    def write(self, record: MeasurementRecord) -> None:
        snapshot = record.snapshot
        line = snapshot.line_pressure_reading
        differential = snapshot.differential_pressure_reading
        self._writer.writerow(
            (
                snapshot.recorded_at.isoformat(),
                self._hu(snapshot.monotonic_seconds),
                self._hu(snapshot.jacket_pump.pressure_bar),
                self._hu(snapshot.jacket_pump.flow_ml_per_hour),
                self._hu(snapshot.jacket_pump.remaining_volume_ml),
                self._hu(record.jacket_net_volume_ml),
                self._hu(snapshot.injection_pump.pressure_bar),
                self._hu(snapshot.injection_pump.flow_ml_per_hour),
                self._hu(snapshot.injection_pump.remaining_volume_ml),
                self._hu(record.injection_net_volume_ml),
                self._hu(snapshot.raw_line_voltage),
                self._hu(None if line is None else line.median_voltage),
                self._hu(None if line is None else line.filtered_voltage),
                self._hu(snapshot.raw_line_pressure_bar),
                self._hu(snapshot.line_pressure_bar),
                snapshot.line_pressure_quality.value,
                snapshot.line_pressure_quality_reason,
                self._hu(snapshot.line_pressure_sample_age_seconds),
                self._hu(snapshot.raw_differential_voltage),
                self._hu(
                    None if differential is None else differential.median_voltage
                ),
                self._hu(
                    None if differential is None else differential.filtered_voltage
                ),
                self._hu(snapshot.raw_differential_pressure_bar),
                self._hu(snapshot.differential_pressure_bar),
                snapshot.differential_pressure_quality.value,
                snapshot.differential_pressure_quality_reason,
                self._hu(snapshot.differential_pressure_sample_age_seconds),
                self._hu(snapshot.valve_percent),
                record.active_stage,
                snapshot.quality.value,
                "|".join(record.safety_reasons),
            )
        )
        self._sync()

    @staticmethod
    def _hu(value: float | None) -> str:
        return "" if value is None else str(value).replace(".", ",")

    def _sync(self) -> None:
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "CsvMeasurementWriter":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
