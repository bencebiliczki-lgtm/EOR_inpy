"""Repeatable project-database benchmark; run on the legacy target host too."""

from __future__ import annotations

import argparse
import json
import tempfile
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter, sleep

from eor_control.data_management import export_measurement_excel
from eor_control.domain import MeasurementRecord, MeasurementSnapshot, PumpStatus
from eor_control.project_database import (
    MeasurementQuery,
    ProjectSQLiteWriter,
    initialize_project_database,
    query_measurements,
)


def _record(timestamp: datetime, second: int) -> MeasurementRecord:
    return MeasurementRecord(
        MeasurementSnapshot(
            timestamp,
            float(second),
            PumpStatus(120.0, 60.0, 250.0),
            PumpStatus(100.0, 120.0, 240.0),
            101.0,
            2.0,
            45.0,
        ),
        second / 3600.0,
        "24 órás mérés",
        second / 7200.0,
    )


def benchmark(output_directory: Path, samples: int) -> dict[str, object]:
    output_directory.mkdir(parents=True, exist_ok=True)
    database = output_directory / "project.sqlite"
    initialize_project_database(
        database,
        project_id=1,
        project_name="Teljesítményteszt",
        created_at=datetime.now(UTC),
    )
    writer = ProjectSQLiteWriter(database)
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    tracemalloc.start()
    write_started = perf_counter()
    for second in range(samples):
        while writer.metrics.current_size >= writer.metrics.warning_limit:
            sleep(0.001)
        writer.write(_record(started_at + timedelta(seconds=second), second))
    writer.close(timeout_seconds=120.0)
    write_seconds = perf_counter() - write_started
    _current, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    metrics = writer.metrics
    query_started = perf_counter()
    queried = query_measurements(
        database,
        MeasurementQuery(
            columns=("recorded_at", "jacket_pressure_bar", "line_pressure_bar"),
            max_points=20_000,
        ),
    )
    query_seconds = perf_counter() - query_started
    excel = output_directory / "Teljesítményteszt.xlsx"
    export_started = perf_counter()
    export_measurement_excel(database, excel)
    export_seconds = perf_counter() - export_started
    return {
        "samples": samples,
        "database_bytes": database.stat().st_size,
        "write_total_s": write_seconds,
        "enqueue_average_ms": metrics.average_enqueue_ms,
        "transaction_average_ms": metrics.average_transaction_ms,
        "transaction_max_ms": metrics.maximum_transaction_ms,
        "queue_maximum_size": metrics.maximum_size,
        "python_peak_memory_bytes": peak_memory,
        "query_returned_rows": len(queried.rows),
        "query_seconds": query_seconds,
        "excel_seconds": export_seconds,
        "excel_bytes": excel.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=86_400)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.samples < 1:
        parser.error("--samples must be positive")
    if arguments.output is None:
        with tempfile.TemporaryDirectory(prefix="eor-storage-benchmark-") as folder:
            print(json.dumps(benchmark(Path(folder), arguments.samples), indent=2))
    else:
        print(json.dumps(benchmark(arguments.output, arguments.samples), indent=2))


if __name__ == "__main__":
    main()
