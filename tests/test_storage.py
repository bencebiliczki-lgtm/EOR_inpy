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
            raw_line_pressure_bar=102.0,
            raw_differential_pressure_bar=2.75,
        ),
        injected_volume_ml=10.0,
        active_stage=stage,
        jacket_net_volume_ml=-2.5,
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
    assert rows[1][CsvMeasurementWriter.HEADER.index("jacket_net_volume_ml")] == "-2,5"
    assert rows[1][CsvMeasurementWriter.HEADER.index("injection_net_volume_ml")] == "10,0"
    assert rows[1][CsvMeasurementWriter.HEADER.index("raw_line_pressure_bar")] == (
        "102,0"
    )
    assert rows[1][
        CsvMeasurementWriter.HEADER.index("raw_differential_pressure_bar")
    ] == "2,75"
    stage_index = CsvMeasurementWriter.HEADER.index("active_stage")
    assert rows[1][stage_index:] == ["water", "good", "example fault"]


def test_csv_writer_appends_without_repeating_header(tmp_path: Path) -> None:
    path = tmp_path / "raw.csv"
    with CsvMeasurementWriter(path) as writer:
        writer.write(record("first"))
    with CsvMeasurementWriter(path) as writer:
        writer.write(record("second"))

    rows = read_rows(path)
    assert len(rows) == 3
    stage_index = CsvMeasurementWriter.HEADER.index("active_stage")
    assert rows[1][stage_index] == "first"
    assert rows[2][stage_index] == "second"


def test_csv_writer_upgrades_legacy_file_and_preserves_backup(tmp_path: Path) -> None:
    path = tmp_path / "legacy_raw.csv"
    legacy = record("legacy")
    snapshot = legacy.snapshot
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter=";", lineterminator="\n")
        writer.writerow(CsvMeasurementWriter.LEGACY_HEADER)
        writer.writerow(
            (
                snapshot.recorded_at.isoformat(),
                "10,5",
                "120,0",
                "0,0",
                "250,0",
                "100,0",
                "12,0",
                "240,0",
                "10,0",
                "101,0",
                "2,5",
                "45,0",
                "legacy",
                "good",
                "",
            )
        )

    with CsvMeasurementWriter(path) as writer:
        writer.write(record("new"))

    rows = read_rows(path)
    assert rows[0] == list(CsvMeasurementWriter.HEADER)
    assert rows[1][CsvMeasurementWriter.HEADER.index("jacket_net_volume_ml")] == ""
    assert rows[1][CsvMeasurementWriter.HEADER.index("injection_net_volume_ml")] == (
        "10,0"
    )
    assert rows[2][CsvMeasurementWriter.HEADER.index("jacket_net_volume_ml")] == (
        "-2,5"
    )
    assert (tmp_path / "legacy_raw_v1_backup.csv").is_file()


def test_csv_writer_upgrades_prefilter_file_and_copies_values_to_raw_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "prefilter.csv"
    values = {name: "1,0" for name in CsvMeasurementWriter.V2_HEADER}
    values["line_pressure_bar"] = "101,0"
    values["differential_pressure_bar"] = "2,5"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter=";", lineterminator="\n")
        writer.writerow(CsvMeasurementWriter.V2_HEADER)
        writer.writerow([values[name] for name in CsvMeasurementWriter.V2_HEADER])

    with CsvMeasurementWriter(path):
        pass

    rows = read_rows(path)
    header = CsvMeasurementWriter.HEADER
    assert rows[1][header.index("raw_line_pressure_bar")] == "101,0"
    assert rows[1][header.index("raw_differential_pressure_bar")] == "2,5"
    assert (tmp_path / "prefilter_v2_backup.csv").is_file()
