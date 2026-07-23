import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, Thread
from time import sleep

from eor_control.domain import MeasurementRecord
from eor_control.storage import CsvMeasurementWriter
from eor_control.timezone import as_hungarian_time


def safe_filename(value: str) -> str:
    """Return a Windows-safe, stable file-name component."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned[:80] or "projekt"


@dataclass(frozen=True, slots=True)
class MeasurementTable:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def column(self, name: str) -> tuple[str, ...]:
        try:
            index = self.header.index(name)
        except ValueError as error:
            raise KeyError(name) from error
        return tuple(row[index] for row in self.rows)


def measurement_stages(table: MeasurementTable) -> tuple[str, ...]:
    """Return non-empty stage names in first-occurrence order."""

    if "active_stage" not in table.header:
        return ()
    return tuple(dict.fromkeys(value for value in table.column("active_stage") if value))


def filter_measurement_table_by_stage(
    table: MeasurementTable, stage: str | None
) -> MeasurementTable:
    if stage is None or "active_stage" not in table.header:
        return table
    stage_index = table.header.index("active_stage")
    return MeasurementTable(
        table.header,
        tuple(row for row in table.rows if row[stage_index] == stage),
    )


def measurement_stage_segments(
    table: MeasurementTable,
) -> tuple[tuple[str, int, int], ...]:
    """Return contiguous stage spans as (name, start, exclusive end)."""

    if not table.rows or "active_stage" not in table.header:
        return ()
    values = table.column("active_stage")
    segments: list[tuple[str, int, int]] = []
    start = 0
    current = values[0]
    for index, stage in enumerate(values[1:], start=1):
        if stage != current:
            segments.append((current, start, index))
            current = stage
            start = index
    segments.append((current, start, len(values)))
    return tuple(segments)


def read_measurement_table(path: Path) -> MeasurementTable:
    if not path.is_file():
        return MeasurementTable(CsvMeasurementWriter.HEADER, ())
    with path.open(encoding="utf-8", newline="") as file:
        first_line = file.readline()
        file.seek(0)
        delimiter = ";" if ";" in first_line else ","
        rows = list(csv.reader(file, delimiter=delimiter))
    if not rows:
        return MeasurementTable(CsvMeasurementWriter.HEADER, ())
    header = tuple(rows[0])
    legacy_inlet_column = "inlet_pressure_bar"
    if (
        legacy_inlet_column in header
        and tuple(item for item in header if item != legacy_inlet_column)
        == CsvMeasurementWriter.LEGACY_HEADER
    ):
        inlet_index = header.index(legacy_inlet_column)
        rows = [
            [value for index, value in enumerate(row) if index != inlet_index]
            for row in rows
        ]
        header = tuple(rows[0])
    if header in (
        CsvMeasurementWriter.LEGACY_HEADER,
        CsvMeasurementWriter.V2_HEADER,
    ):
        legacy_index = {name: index for index, name in enumerate(header)}
        converted_rows: list[list[str]] = [list(CsvMeasurementWriter.HEADER)]
        for row in rows[1:]:
            converted_rows.append(
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
                    for name in CsvMeasurementWriter.HEADER
                ]
            )
        rows = converted_rows
        header = CsvMeasurementWriter.HEADER
    if header != CsvMeasurementWriter.HEADER:
        raise ValueError("a mérési CSV fejléce nem támogatott")
    width = len(header)
    valid_rows = tuple(tuple(row) for row in rows[1:] if len(row) == width)
    return MeasurementTable(header, valid_rows)


def read_measurement_tables(paths: Iterable[Path]) -> MeasurementTable:
    """Combine phase CSV files in memory without creating a merged data file."""

    rows: list[tuple[str, ...]] = []
    for path in dict.fromkeys(paths):
        rows.extend(read_measurement_table(path).rows)

    def recorded_at(row: tuple[str, ...]) -> datetime:
        try:
            return datetime.fromisoformat(row[0].replace("Z", "+00:00")).astimezone(UTC)
        except (IndexError, ValueError):
            return datetime.max.replace(tzinfo=UTC)

    rows.sort(key=recorded_at)
    return MeasurementTable(CsvMeasurementWriter.HEADER, tuple(rows))


def export_measurement_csv(
    source: Path,
    destination: Path,
    *,
    decimal_comma: bool = True,
    delimiter: str = ";",
) -> None:
    if delimiter not in {",", ";", "\t"}:
        raise ValueError("a CSV elválasztó csak vessző, pontosvessző vagy tabulátor lehet")
    table = read_measurement_table(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    numeric_columns = set(range(1, table.header.index("active_stage")))
    with destination.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter=delimiter, lineterminator="\n")
        writer.writerow(table.header)
        for row in table.rows:
            values = list(row)
            for index in numeric_columns:
                if decimal_comma:
                    values[index] = values[index].replace(".", ",")
                else:
                    values[index] = values[index].replace(",", ".")
            writer.writerow(values)


def project_excel_path(source: Path, stage_name: str) -> Path:
    """Return the single project workbook path for a stage raw-data file."""
    suffix = f"_{safe_filename(stage_name)}_live_raw.csv"
    if not source.name.endswith(suffix):
        raise ValueError(
            "a mérési szakasz fájlneve nem illeszkedik a projekt-exporthoz"
        )
    project_name = source.name[: -len(suffix)]
    if not project_name:
        raise ValueError("a projekt Excel-fájlneve nem lehet üres")
    return source.with_name(f"{project_name}.xlsx")


def _excel_sheet_title(stage_name: str) -> str:
    requested = stage_name.strip() or "Mérés"
    cleaned = re.sub(r"[\[\]:*?/\\]", "_", requested).strip("'") or "Mérés"
    if cleaned == requested and len(cleaned) <= 31:
        return cleaned
    digest = hashlib.sha256(requested.encode("utf-8")).hexdigest()[:6]
    return f"{cleaned[:24]}_{digest}"


def export_measurement_excel(
    source: Path,
    destination: Path,
    *,
    stage_name: str,
) -> None:
    """Create or update one stage worksheet in a project workbook."""
    try:
        from openpyxl import Workbook  # type: ignore[import-untyped]
        from openpyxl.chart import LineChart, Reference  # type: ignore[import-untyped]
        from openpyxl.reader.excel import load_workbook  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(
            "Az Excel-exporthoz telepítsd az export függőséget: pip install -e \".[export]\""
        ) from error

    table = read_measurement_table(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        workbook = load_workbook(destination)
    else:
        workbook = Workbook()
        workbook.remove(workbook.active)

    sheet_title = _excel_sheet_title(stage_name)
    sheet_index = len(workbook.worksheets)
    if sheet_title in workbook.sheetnames:
        existing = workbook[sheet_title]
        sheet_index = workbook.index(existing)
        workbook.remove(existing)
    sheet = workbook.create_sheet(sheet_title, sheet_index)
    sheet.freeze_panes = "A2"
    sheet.append(list(table.header))
    numeric_columns = set(range(1, table.header.index("active_stage")))
    for source_row in table.rows:
        row: list[str | float] = list(source_row)
        for index in numeric_columns:
            with suppress(ValueError):
                row[index] = float(source_row[index].replace(",", "."))
        sheet.append(row)
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = column[0].column_letter
        width = min(42, max(len(str(cell.value or "")) for cell in column) + 2)
        sheet.column_dimensions[letter].width = width

    if table.rows:
        chart = LineChart()
        chart.title = f"{stage_name} — nyomás- és szelepdiagram"
        chart.y_axis.title = "bar / %"
        chart.x_axis.title = "Minta"
        categories = Reference(sheet, min_col=1, min_row=2, max_row=len(table.rows) + 1)
        for column_name in (
            "jacket_pressure_bar",
            "injection_pressure_bar",
            "line_pressure_bar",
            "differential_pressure_bar",
            "valve_percent",
        ):
            column_index = table.header.index(column_name) + 1
            data = Reference(
                sheet,
                min_col=column_index,
                max_col=column_index,
                min_row=1,
                max_row=len(table.rows) + 1,
            )
            chart.add_data(data, titles_from_data=True, from_rows=False)
        chart.set_categories(categories)
        chart.height = 10
        chart.width = 24
        sheet.add_chart(chart, "T2")

    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    workbook.save(temporary)
    os.replace(temporary, destination)


@dataclass(frozen=True, slots=True)
class NasQueueItem:
    relative_path: str
    source_path: Path
    revision: int
    attempts: int
    last_error: str


class NasSyncQueue:
    """Persistent SQLite queue; survives application and network failures."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = Lock()
        with self._lock, self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS nas_sync_queue (
                    relative_path TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    queued_at_utc TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                str(row[1])
                for row in self._connection.execute("PRAGMA table_info(nas_sync_queue)")
            }
            if "revision" not in columns:
                self._connection.execute(
                    "ALTER TABLE nas_sync_queue ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
                )

    def enqueue(self, source_path: Path, relative_path: Path) -> None:
        relative = _validated_relative_path(relative_path)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO nas_sync_queue (
                    relative_path, source_path, queued_at_utc, revision, attempts, last_error
                ) VALUES (?, ?, ?, 1, 0, '')
                ON CONFLICT(relative_path) DO UPDATE SET
                    source_path=excluded.source_path,
                    queued_at_utc=excluded.queued_at_utc,
                    revision=nas_sync_queue.revision + 1
                """,
                (relative.as_posix(), str(source_path.resolve()), datetime.now(UTC).isoformat()),
            )

    def pending(self) -> tuple[NasQueueItem, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT relative_path, source_path, revision, attempts, last_error
                FROM nas_sync_queue ORDER BY queued_at_utc
                """
            ).fetchall()
        return tuple(
            NasQueueItem(
                str(row[0]), Path(str(row[1])), int(row[2]), int(row[3]), str(row[4])
            )
            for row in rows
        )

    def complete(self, relative_path: str, revision: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM nas_sync_queue WHERE relative_path = ? AND revision = ?",
                (relative_path, revision),
            )

    def fail(self, relative_path: str, message: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE nas_sync_queue
                SET attempts = attempts + 1, last_error = ?
                WHERE relative_path = ?
                """,
                (message[:1000], relative_path),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _validated_relative_path(path: Path) -> Path:
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("a NAS célútvonalnak biztonságos relatív útvonalnak kell lennie")
    return path


class BackgroundNasSynchronizer:
    def __init__(
        self,
        queue: NasSyncQueue,
        *,
        retry_interval_seconds: float = 30.0,
    ) -> None:
        if retry_interval_seconds <= 0.0:
            raise ValueError("a NAS újrapróbálkozási időnek pozitívnak kell lennie")
        self._queue = queue
        self._retry_interval = retry_interval_seconds
        self._target_root: Path | None = None
        self._enabled = False
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._sync_lock = Lock()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def target_root(self) -> Path | None:
        with self._lock:
            return self._target_root

    @property
    def pending_count(self) -> int:
        return len(self._queue.pending())

    def configure(self, *, enabled: bool, target_root: Path | None) -> None:
        if enabled and target_root is None:
            raise ValueError("engedélyezett NAS-mentéshez célmappa szükséges")
        with self._lock:
            self._enabled = enabled
            self._target_root = target_root
        if enabled:
            self.start()
            self._wake.set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="eor-nas-sync", daemon=True)
        self._thread.start()

    def enqueue(self, source_path: Path, relative_path: Path) -> None:
        if not self.enabled:
            return
        self._queue.enqueue(source_path, relative_path)
        self.start()
        self._wake.set()

    def sync_pending_once(self) -> int:
        with self._sync_lock:
            with self._lock:
                enabled = self._enabled
                target_root = self._target_root
            if not enabled or target_root is None:
                return 0
            completed = 0
            for item in self._queue.pending():
                try:
                    if not item.source_path.is_file():
                        raise FileNotFoundError(
                            f"forrásfájl nem található: {item.source_path}"
                        )
                    relative = _validated_relative_path(Path(item.relative_path))
                    destination = target_root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(f".{destination.name}.eor-sync.tmp")
                    shutil.copy2(item.source_path, temporary)
                    os.replace(temporary, destination)
                    self._queue.complete(item.relative_path, item.revision)
                    completed += 1
                except OSError as error:
                    self._queue.fail(item.relative_path, str(error))
            return completed

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sync_pending_once()
            self._wake.wait(self._retry_interval)
            self._wake.clear()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None
        self._queue.close()


class ProjectMeasurementWriter:
    """Routes raw records to one crash-safe file per project measurement phase."""

    def __init__(
        self,
        data_root: Path,
        nas_sync: BackgroundNasSynchronizer | None = None,
        *,
        enabled: bool = True,
        phase_completed: Callable[[Path, str], None] | None = None,
    ) -> None:
        self._data_root = data_root
        self._nas_sync = nas_sync
        self._enabled = enabled
        self._phase_completed = phase_completed
        self._writer: CsvMeasurementWriter | None = None
        self._path: Path | None = None
        self._relative_path: Path | None = None
        self._relative_folder: Path | None = None
        self._project_file_prefix: str | None = None
        self._stage_name: str | None = None
        self._phase_has_records = False
        self._lock = Lock()

    @property
    def current_path(self) -> Path | None:
        with self._lock:
            return self._path if self._enabled else None

    @property
    def persistence_enabled(self) -> bool:
        return self._enabled

    @property
    def phase_paths(self) -> tuple[Path, ...]:
        with self._lock:
            relative_folder = self._relative_folder
        if relative_folder is None:
            return ()
        folder = self._data_root / relative_folder
        return tuple(sorted(folder.glob("*_live_raw.csv")))

    def select_project(
        self, project_id: int, project_name: str, *, stage_name: str = "Mérés"
    ) -> Path:
        return self.select_project_with_metadata(
            project_id, project_name, stage_name=stage_name
        )

    def select_project_with_metadata(
        self,
        project_id: int,
        project_name: str,
        *,
        created_at: datetime | None = None,
        notes: str = "",
        configuration: dict[str, object] | None = None,
        calibration_snapshot: dict[str, object] | None = None,
        stages: list[dict[str, object]] | None = None,
        stage_name: str = "Mérés",
    ) -> Path:
        if project_id <= 0:
            raise ValueError("érvénytelen projektazonosító")
        timestamp = created_at or datetime.now(UTC)
        local_timestamp = as_hungarian_time(timestamp)
        folder = (
            f"{local_timestamp:%Y-%m-%d}_{project_id:06d}_{safe_filename(project_name)}"
        )
        relative_folder = Path("projects") / str(local_timestamp.year) / folder
        project_file_prefix = safe_filename(project_name)
        relative_path = relative_folder / (
            f"{project_file_prefix}_{safe_filename(stage_name)}_live_raw.csv"
        )
        path = self._data_root / relative_path
        completed_phase: tuple[Path, str] | None = None
        with self._lock:
            if path == self._path and (self._writer is not None or not self._enabled):
                return path
            if self._writer is not None:
                self._writer.close()
                if (
                    self._phase_has_records
                    and self._path is not None
                    and self._stage_name is not None
                ):
                    completed_phase = (self._path, self._stage_name)
            self._writer = CsvMeasurementWriter(path) if self._enabled else None
            self._path = path
            self._relative_path = relative_path
            self._relative_folder = relative_folder
            self._project_file_prefix = project_file_prefix
            self._stage_name = stage_name
            self._phase_has_records = False
        if completed_phase is not None and self._phase_completed is not None:
            self._phase_completed(*completed_phase)
        if not self._enabled:
            return path
        self._write_project_snapshots(
            relative_folder,
            project_id=project_id,
            project_name=project_name,
            created_at=timestamp,
            notes=notes,
            configuration=configuration or {},
            calibration_snapshot=calibration_snapshot or {},
            stages=stages or [],
        )
        return path

    def _write_project_snapshots(
        self,
        relative_folder: Path,
        *,
        project_id: int,
        project_name: str,
        created_at: datetime,
        notes: str,
        configuration: dict[str, object],
        calibration_snapshot: dict[str, object],
        stages: list[dict[str, object]],
    ) -> None:
        documents = {
            "project.json": {
                "id": project_id,
                "name": project_name,
                "measurement_kind": "live",
                "created_at_utc": created_at.astimezone(UTC).isoformat(),
                "notes": notes,
                "stages": stages,
            },
            "config_snapshot.json": configuration,
            "calibration_snapshot.json": calibration_snapshot,
        }
        folder = self._data_root / relative_folder
        folder.mkdir(parents=True, exist_ok=True)
        for filename, payload in documents.items():
            destination = folder / filename
            temporary = destination.with_suffix(f"{destination.suffix}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, destination)
            if self._nas_sync is not None:
                self._nas_sync.enqueue(
                    destination, Path(*relative_folder.parts[1:]) / filename
                )

    def write(self, record: MeasurementRecord) -> None:
        if not self._enabled:
            return
        completed_phase: tuple[Path, str] | None = None
        with self._lock:
            if self._writer is None or self._path is None or self._relative_path is None:
                raise RuntimeError("a mérés előtt projektet kell kiválasztani")
            if record.active_stage != self._stage_name:
                completed_phase = self._open_phase_locked(record.active_stage)
            assert self._writer is not None
            assert self._path is not None
            assert self._relative_path is not None
            self._writer.write(record)
            self._phase_has_records = True
            path = self._path
            relative_path = self._relative_path
        if completed_phase is not None and self._phase_completed is not None:
            self._phase_completed(*completed_phase)
        if self._nas_sync is not None:
            self._nas_sync.enqueue(path, Path(*relative_path.parts[1:]))

    def _open_phase_locked(self, stage_name: str) -> tuple[Path, str] | None:
        if self._relative_folder is None or self._project_file_prefix is None:
            raise RuntimeError("a mérés előtt projektet kell kiválasztani")
        completed_phase = (
            (self._path, self._stage_name)
            if (
                self._phase_has_records
                and self._path is not None
                and self._stage_name is not None
            )
            else None
        )
        relative_path = self._relative_folder / (
            f"{self._project_file_prefix}_{safe_filename(stage_name)}_live_raw.csv"
        )
        path = self._data_root / relative_path
        if self._writer is not None:
            self._writer.close()
        self._writer = CsvMeasurementWriter(path)
        self._path = path
        self._relative_path = relative_path
        self._stage_name = stage_name
        self._phase_has_records = False
        return completed_phase

    def complete_current_phase(self) -> Path | None:
        completed_phase: tuple[Path, str] | None = None
        with self._lock:
            if self._writer is not None:
                self._writer.close()
                self._writer = None
            if (
                self._phase_has_records
                and self._path is not None
                and self._stage_name is not None
            ):
                completed_phase = (self._path, self._stage_name)
            self._phase_has_records = False
        if completed_phase is not None and self._phase_completed is not None:
            self._phase_completed(*completed_phase)
        return completed_phase[0] if completed_phase is not None else None

    def close(self) -> None:
        with self._lock:
            if self._writer is not None:
                self._writer.close()
            self._writer = None
            self._phase_has_records = False


def numeric_series(
    table: MeasurementTable, names: Iterable[str]
) -> dict[str, tuple[float, ...]]:
    result: dict[str, tuple[float, ...]] = {}
    for name in names:
        values: list[float] = []
        for value in table.column(name):
            try:
                values.append(float(value.replace(",", ".")))
            except ValueError:
                values.append(float("nan"))
        result[name] = tuple(values)
    return result


def wait_for_sync(synchronizer: BackgroundNasSynchronizer, timeout_seconds: float) -> bool:
    """Small test/CLI helper; the UI itself never blocks on NAS."""
    deadline_steps = max(1, int(timeout_seconds / 0.01))
    for _ in range(deadline_steps):
        if synchronizer.pending_count == 0:
            return True
        sleep(0.01)
    return synchronizer.pending_count == 0
