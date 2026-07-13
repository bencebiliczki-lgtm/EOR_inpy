import csv
from datetime import UTC, datetime
from pathlib import Path

from eor_control.domain import MeasurementRecord, MeasurementSnapshot, PumpStatus
from eor_control.storage import CsvMeasurementWriter


def record(stage: str = "water") -> MeasurementRecord:
    return MeasurementRecord(
        snapshot=MeasurementSnapshot(
            recorded_at=datetime(2026, 7, 13, 12, 30, tzinfo=UTC),
            monotonic_seconds=10.5,
            jacket_pump=PumpStatus(120.0, 0.0, 250.0),
            injection_pump=PumpStatus(100.0, 12.0, 240.0),
            line_pressure_bar=101.0,
            differential_pressure_bar=2.5,
            valve_percent=45.0,
        ),
        injected_volume_ml=10.0,
        active_stage=stage,
        safety_reasons=("example fault",),
    )


def read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.reader(file, delimiter=";"))


def test_csv_writer_persists_header_and_measurement(tmp_path: Path) -> None:
    path = tmp_path / "raw.csv"

    with CsvMeasurementWriter(path) as writer:
        writer.write(record())

    rows = read_rows(path)
    assert rows[0] == list(CsvMeasurementWriter.HEADER)
    assert rows[1][0] == "2026-07-13T12:30:00+00:00"
    assert rows[1][8] == "10,0"
    assert rows[1][13:] == ["water", "good", "example fault"]


def test_csv_writer_appends_without_repeating_header(tmp_path: Path) -> None:
    path = tmp_path / "raw.csv"
    with CsvMeasurementWriter(path) as writer:
        writer.write(record("first"))
    with CsvMeasurementWriter(path) as writer:
        writer.write(record("second"))

    rows = read_rows(path)
    assert len(rows) == 3
    assert rows[1][13] == "first"
    assert rows[2][13] == "second"
