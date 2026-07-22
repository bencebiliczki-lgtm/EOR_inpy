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
    HEADER = (
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
        if header not in (cls.LEGACY_HEADER, cls.V2_HEADER):
            raise ValueError("a meglévő mérési CSV fejléce nem támogatott")
        backup_version = "v1" if header == cls.LEGACY_HEADER else "v2"
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
                self._hu(snapshot.raw_line_pressure_bar),
                self._hu(snapshot.line_pressure_bar),
                self._hu(snapshot.raw_differential_pressure_bar),
                self._hu(snapshot.differential_pressure_bar),
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
