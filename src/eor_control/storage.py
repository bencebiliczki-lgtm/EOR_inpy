import csv
import os
from pathlib import Path
from typing import Protocol, TextIO

from eor_control.domain import MeasurementRecord


class MeasurementWriter(Protocol):
    def write(self, record: MeasurementRecord) -> None: ...

    def close(self) -> None: ...


class CsvMeasurementWriter:
    """Append-only raw writer that flushes every complete measurement row."""

    HEADER = (
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

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        is_empty = not path.exists() or path.stat().st_size == 0
        self._file: TextIO = path.open("a", encoding="utf-8", newline="")
        self._writer = csv.writer(self._file, lineterminator="\n")
        if is_empty:
            self._writer.writerow(self.HEADER)
            self._sync()

    def write(self, record: MeasurementRecord) -> None:
        snapshot = record.snapshot
        self._writer.writerow(
            (
                snapshot.recorded_at.isoformat(),
                snapshot.monotonic_seconds,
                snapshot.jacket_pump.pressure_bar,
                snapshot.jacket_pump.flow_ml_per_hour,
                snapshot.jacket_pump.remaining_volume_ml,
                snapshot.injection_pump.pressure_bar,
                snapshot.injection_pump.flow_ml_per_hour,
                snapshot.injection_pump.remaining_volume_ml,
                record.injected_volume_ml,
                snapshot.line_pressure_bar,
                snapshot.differential_pressure_bar,
                snapshot.valve_percent,
                record.active_stage,
                snapshot.quality.value,
                "|".join(record.safety_reasons),
            )
        )
        self._sync()

    def _sync(self) -> None:
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "CsvMeasurementWriter":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
