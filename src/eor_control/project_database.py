"""Per-project SQLite storage for measurement telemetry and events.

The control loop only enqueues immutable messages.  One dedicated worker owns
the write connection and commits bounded batches, while readers use their own
connections and can continue in WAL mode during a measurement.
"""

from __future__ import annotations

import json
import queue
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, perf_counter
from typing import Final

from eor_control import __version__
from eor_control.domain import DataQuality, MeasurementRecord

SCHEMA_VERSION: Final = 3
REQUIRED_TABLES: Final = frozenset(
    {"schema_info", "project", "phases", "measurements", "events", "export_history"}
)
REQUIRED_INDEXES: Final = frozenset(
    {
        "idx_measurements_phase_time",
        "idx_measurements_recorded_at",
        "idx_events_phase_time",
        "idx_events_type_time",
    }
)


class ProjectDatabaseError(RuntimeError):
    """Base error for a per-project database failure."""


class PersistenceQueueFullError(ProjectDatabaseError):
    """The bounded persistence queue reached its critical limit."""


class PersistenceWriterError(ProjectDatabaseError):
    """The background writer failed and could not durably store its batch."""


@dataclass(frozen=True, slots=True)
class QueueMetrics:
    capacity: int
    warning_limit: int
    critical_limit: int
    current_size: int
    maximum_size: int
    enqueued_count: int
    committed_count: int
    average_enqueue_ms: float
    average_transaction_ms: float
    maximum_transaction_ms: float
    last_error: str


@dataclass(frozen=True, slots=True)
class PhaseRow:
    id: int
    sequence: int
    name: str
    phase_type: str
    started_at: str
    ended_at: str | None
    status: str


@dataclass(frozen=True, slots=True)
class MeasurementQuery:
    phase_id: int | None = None
    start_at: str | None = None
    end_at: str | None = None
    columns: tuple[str, ...] = ()
    max_points: int | None = None
    limit: int | None = None
    offset: int = 0


@dataclass(frozen=True, slots=True)
class MeasurementRows:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True, slots=True)
class MigrationReport:
    source_files: int
    source_rows: int
    inserted_rows: int
    duplicate_rows: int
    invalid_rows: int
    warnings: tuple[str, ...]


_MEASUREMENT_COLUMNS: Final = (
    "id",
    "phase_id",
    "recorded_at",
    "project_elapsed_s",
    "phase_elapsed_s",
    "jacket_pressure_bar",
    "jacket_flow_ml_min",
    "jacket_remaining_volume_ml",
    "jacket_injected_volume_ml",
    "injection_pressure_bar",
    "injection_flow_ml_min",
    "injection_remaining_volume_ml",
    "injection_injected_volume_ml",
    "line_pressure_bar",
    "differential_pressure_bar",
    "valve_position_percent",
    "jacket_data_quality",
    "injection_data_quality",
    "pressure_data_quality",
    "valve_data_quality",
    "jacket_sample_age_s",
    "injection_sample_age_s",
    "pressure_sample_age_s",
    "valve_sample_age_s",
    "safety_state",
    "diagnostic_flags",
    "control_state",
    "raw_line_voltage",
    "median_line_voltage",
    "filtered_line_voltage",
    "raw_line_pressure_bar",
    "line_data_quality",
    "line_quality_reason",
    "line_sample_age_s",
    "raw_differential_voltage",
    "median_differential_voltage",
    "filtered_differential_voltage",
    "raw_differential_pressure_bar",
    "differential_data_quality",
    "differential_quality_reason",
    "differential_sample_age_s",
    "source_key",
)
_QUERYABLE_COLUMNS: Final = frozenset(_MEASUREMENT_COLUMNS)


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if not read_only:
        mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
        if mode != "wal":
            connection.close()
            raise ProjectDatabaseError(f"a WAL naplózási mód nem aktiválható: {mode}")
        connection.execute("PRAGMA synchronous = FULL")
    return connection


def initialize_project_database(
    path: Path,
    *,
    project_id: int,
    project_name: str,
    created_at: datetime,
    notes: str = "",
    settings: dict[str, object] | None = None,
    units: dict[str, str] | None = None,
) -> None:
    """Create or validate a project's complete local database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    connection = _connect(path)
    try:
        with connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    schema_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    last_migration_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                    sample_name TEXT,
                    sample_id TEXT,
                    created_at TEXT NOT NULL,
                    measurement_started_at TEXT,
                    measurement_ended_at TEXT,
                    status TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    units_json TEXT NOT NULL,
                    app_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS phases (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES project(id),
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                    phase_type TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    source_key TEXT UNIQUE,
                    UNIQUE(project_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS measurements (
                    id INTEGER PRIMARY KEY,
                    phase_id INTEGER NOT NULL REFERENCES phases(id),
                    recorded_at TEXT NOT NULL,
                    project_elapsed_s REAL NOT NULL CHECK(project_elapsed_s >= 0),
                    phase_elapsed_s REAL NOT NULL CHECK(phase_elapsed_s >= 0),
                    jacket_pressure_bar REAL,
                    jacket_flow_ml_min REAL,
                    jacket_remaining_volume_ml REAL,
                    jacket_injected_volume_ml REAL,
                    injection_pressure_bar REAL,
                    injection_flow_ml_min REAL,
                    injection_remaining_volume_ml REAL,
                    injection_injected_volume_ml REAL,
                    line_pressure_bar REAL,
                    differential_pressure_bar REAL,
                    valve_position_percent REAL,
                    jacket_data_quality TEXT,
                    injection_data_quality TEXT,
                    pressure_data_quality TEXT,
                    valve_data_quality TEXT,
                    jacket_sample_age_s REAL,
                    injection_sample_age_s REAL,
                    pressure_sample_age_s REAL,
                    valve_sample_age_s REAL,
                    safety_state TEXT,
                    diagnostic_flags TEXT NOT NULL,
                    control_state TEXT,
                    raw_line_voltage REAL,
                    median_line_voltage REAL,
                    filtered_line_voltage REAL,
                    raw_line_pressure_bar REAL,
                    line_data_quality TEXT,
                    line_quality_reason TEXT,
                    line_sample_age_s REAL,
                    raw_differential_voltage REAL,
                    median_differential_voltage REAL,
                    filtered_differential_voltage REAL,
                    raw_differential_pressure_bar REAL,
                    differential_data_quality TEXT,
                    differential_quality_reason TEXT,
                    differential_sample_age_s REAL,
                    source_key TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    phase_id INTEGER REFERENCES phases(id),
                    recorded_at TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    measurement_id INTEGER REFERENCES measurements(id)
                );
                CREATE TABLE IF NOT EXISTS export_history (
                    id INTEGER PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    export_type TEXT NOT NULL,
                    target_file TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phases_count INTEGER NOT NULL DEFAULT 0,
                    rows_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_measurements_phase_time
                ON measurements(phase_id, recorded_at);
                CREATE INDEX IF NOT EXISTS idx_measurements_recorded_at
                ON measurements(recorded_at);
                CREATE INDEX IF NOT EXISTS idx_events_phase_time
                ON events(phase_id, recorded_at);
                CREATE INDEX IF NOT EXISTS idx_events_type_time
                ON events(event_type, recorded_at);
                """
            )
            connection.execute(
                """
                INSERT INTO schema_info (
                    id, schema_version, created_at, app_version, last_migration_version
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (SCHEMA_VERSION, now, __version__, SCHEMA_VERSION),
            )
            version = int(
                connection.execute(
                    "SELECT schema_version FROM schema_info WHERE id=1"
                ).fetchone()[0]
            )
            if version == 1:
                connection.execute(
                    "ALTER TABLE measurements ADD COLUMN median_line_voltage REAL"
                )
                connection.execute(
                    "ALTER TABLE measurements ADD COLUMN filtered_line_voltage REAL"
                )
                connection.execute(
                    "ALTER TABLE measurements ADD COLUMN line_data_quality TEXT"
                )
                connection.execute(
                    "ALTER TABLE measurements ADD COLUMN line_quality_reason TEXT"
                )
                connection.execute(
                    "ALTER TABLE measurements ADD COLUMN line_sample_age_s REAL"
                )
                connection.execute(
                    "UPDATE measurements SET median_line_voltage=raw_line_voltage, "
                    "line_data_quality=COALESCE(pressure_data_quality, 'good'), "
                    "line_quality_reason='', line_sample_age_s=pressure_sample_age_s"
                )
                connection.execute(
                    "UPDATE schema_info SET schema_version=2, "
                    "last_migration_version=2, app_version=? WHERE id=1",
                    (__version__,),
                )
                version = 2
            if version == 2:
                for definition in (
                    "median_differential_voltage REAL",
                    "filtered_differential_voltage REAL",
                    "differential_data_quality TEXT",
                    "differential_quality_reason TEXT",
                    "differential_sample_age_s REAL",
                ):
                    connection.execute(
                        f"ALTER TABLE measurements ADD COLUMN {definition}"
                    )
                connection.execute(
                    "UPDATE measurements SET "
                    "median_differential_voltage=raw_differential_voltage, "
                    "differential_data_quality="
                    "COALESCE(pressure_data_quality, 'good'), "
                    "differential_quality_reason='', "
                    "differential_sample_age_s=pressure_sample_age_s"
                )
                connection.execute(
                    "UPDATE schema_info SET schema_version=?, "
                    "last_migration_version=?, app_version=? WHERE id=1",
                    (SCHEMA_VERSION, SCHEMA_VERSION, __version__),
                )
            elif version != SCHEMA_VERSION:
                raise ProjectDatabaseError(
                    f"nem tÃ¡mogatott projektsÃ©ma: {version}; "
                    f"elvÃ¡rt: {SCHEMA_VERSION}"
                )
            connection.execute(
                "UPDATE project SET status='interrupted' WHERE status='running'"
            )
            connection.execute(
                """
                INSERT INTO project (
                    id, name, created_at, status, notes, settings_json, units_json, app_version
                ) VALUES (?, ?, ?, 'ready', ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    notes=excluded.notes,
                    settings_json=excluded.settings_json,
                    units_json=excluded.units_json,
                    app_version=excluded.app_version
                """,
                (
                    project_id,
                    project_name,
                    created_at.astimezone(UTC).isoformat(),
                    notes,
                    json.dumps(settings or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(units or default_units(), ensure_ascii=False, sort_keys=True),
                    __version__,
                ),
            )
        validate_project_database(path)
    finally:
        connection.close()


def default_units() -> dict[str, str]:
    return {
        "recorded_at": "UTC ISO-8601",
        "elapsed": "s",
        "pressure": "bar",
        "flow": "mL/min",
        "volume": "mL",
        "valve_position": "%",
    }


def validate_project_database(path: Path) -> None:
    connection = _connect(path, read_only=True)
    try:
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if integrity != "ok":
            raise ProjectDatabaseError(f"sérült projektadatbázis: {integrity}")
        version_row = connection.execute(
            "SELECT schema_version FROM schema_info WHERE id = 1"
        ).fetchone()
        if version_row is None or int(version_row[0]) != SCHEMA_VERSION:
            found = "hiányzik" if version_row is None else str(version_row[0])
            raise ProjectDatabaseError(
                f"nem támogatott projektséma: {found}; elvárt: {SCHEMA_VERSION}"
            )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        if missing := REQUIRED_TABLES - tables:
            raise ProjectDatabaseError(f"hiányzó projekttáblák: {', '.join(sorted(missing))}")
        if missing := REQUIRED_INDEXES - indexes:
            raise ProjectDatabaseError(f"hiányzó projektindexek: {', '.join(sorted(missing))}")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise ProjectDatabaseError("érvénytelen idegen kulcs található a projektadatbázisban")
    finally:
        connection.close()


@dataclass(frozen=True, slots=True)
class _MeasurementMessage:
    record: MeasurementRecord
    enqueued_at: float


@dataclass(frozen=True, slots=True)
class _EventMessage:
    event_id: str
    recorded_at: str
    severity: str
    event_type: str
    source: str
    message: str
    details_json: str


@dataclass(frozen=True, slots=True)
class _CompletePhaseMessage:
    completed_at: str


@dataclass(frozen=True, slots=True)
class _StopMessage:
    pass


_WriterMessage = _MeasurementMessage | _EventMessage | _CompletePhaseMessage | _StopMessage


class ProjectSQLiteWriter:
    """Bounded non-blocking front end with one SQLite writer thread."""

    def __init__(
        self,
        path: Path,
        *,
        queue_capacity: int = 4096,
        warning_ratio: float = 0.5,
        critical_ratio: float = 0.9,
        batch_interval_seconds: float = 0.5,
        batch_size: int = 256,
    ) -> None:
        if queue_capacity < 4 or not 0 < warning_ratio < critical_ratio <= 1:
            raise ValueError("érvénytelen perzisztencia-várólista beállítás")
        if not 0 < batch_interval_seconds <= 1.0 or batch_size < 1:
            raise ValueError("érvénytelen SQLite kötegbeállítás")
        validate_project_database(path)
        self._path = path
        self._queue: queue.Queue[_WriterMessage] = queue.Queue(queue_capacity)
        self._warning_limit = max(1, int(queue_capacity * warning_ratio))
        self._critical_limit = max(self._warning_limit + 1, int(queue_capacity * critical_ratio))
        self._critical_limit = min(queue_capacity, self._critical_limit)
        self._batch_interval = batch_interval_seconds
        self._batch_size = batch_size
        self._stopped = Event()
        self._metrics_lock = Lock()
        self._error: BaseException | None = None
        self._maximum_size = 0
        self._enqueued_count = 0
        self._committed_count = 0
        self._enqueue_total_seconds = 0.0
        self._transaction_count = 0
        self._transaction_total_seconds = 0.0
        self._transaction_max_seconds = 0.0
        self._thread = Thread(target=self._run, name="eor-project-sqlite-writer", daemon=True)
        self._thread.start()

    @property
    def metrics(self) -> QueueMetrics:
        with self._metrics_lock:
            enqueue_average = (
                self._enqueue_total_seconds / self._enqueued_count
                if self._enqueued_count
                else 0.0
            )
            transaction_average = (
                self._transaction_total_seconds / self._transaction_count
                if self._transaction_count
                else 0.0
            )
            return QueueMetrics(
                self._queue.maxsize,
                self._warning_limit,
                self._critical_limit,
                self._queue.qsize(),
                self._maximum_size,
                self._enqueued_count,
                self._committed_count,
                enqueue_average * 1000,
                transaction_average * 1000,
                self._transaction_max_seconds * 1000,
                "" if self._error is None else str(self._error),
            )

    def write(self, record: MeasurementRecord) -> None:
        self._enqueue(_MeasurementMessage(record, monotonic()))

    def write_event(
        self,
        *,
        event_id: str,
        recorded_at: str,
        severity: str,
        event_type: str,
        source: str,
        message: str,
        details: dict[str, object],
    ) -> None:
        self._enqueue(
            _EventMessage(
                event_id,
                recorded_at,
                severity,
                event_type,
                source,
                message,
                json.dumps(details, ensure_ascii=False, sort_keys=True),
            )
        )

    def complete_phase(self, completed_at: datetime | None = None) -> None:
        timestamp = (completed_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        self._enqueue(_CompletePhaseMessage(timestamp))

    def _enqueue(self, message: _WriterMessage) -> None:
        self._raise_if_failed()
        if self._stopped.is_set():
            raise PersistenceWriterError("a projektadatbázis írószála már leállt")
        started = perf_counter()
        if self._queue.qsize() >= self._critical_limit:
            raise PersistenceQueueFullError(
                "a mérési adatmentés kritikus várólistaszintről nem tud helyreállni"
            )
        try:
            self._queue.put_nowait(message)
        except queue.Full as error:
            raise PersistenceQueueFullError("a mérési adatmentés várólistája megtelt") from error
        elapsed = perf_counter() - started
        with self._metrics_lock:
            self._enqueued_count += 1
            self._enqueue_total_seconds += elapsed
            self._maximum_size = max(self._maximum_size, self._queue.qsize())

    def flush(self, timeout_seconds: float = 10.0) -> None:
        deadline = monotonic() + timeout_seconds
        while self._queue.unfinished_tasks:
            self._raise_if_failed()
            if monotonic() >= deadline:
                raise PersistenceWriterError("az SQLite írósor ürítése túllépte az időkorlátot")
            self._stopped.wait(0.01)
        self._raise_if_failed()

    def close(self, timeout_seconds: float = 10.0) -> None:
        if self._stopped.is_set():
            self._raise_if_failed()
            return
        self.flush(timeout_seconds)
        self._queue.put_nowait(_StopMessage())
        self._thread.join(timeout_seconds)
        if self._thread.is_alive():
            raise PersistenceWriterError("az SQLite írószál nem állt le időben")
        self._stopped.set()
        self._raise_if_failed()

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            raise PersistenceWriterError(
                f"projektadatbázis-írási hiba: {self._error}"
            ) from self._error

    def _run(self) -> None:
        connection: sqlite3.Connection | None = None
        active_phase_id: int | None = None
        active_phase_name: str | None = None
        phase_started: datetime | None = None
        project_started: datetime | None = None
        try:
            connection = _connect(self._path)
            row = connection.execute(
                "SELECT measurement_started_at FROM project LIMIT 1"
            ).fetchone()
            if row is not None and row[0]:
                project_started = datetime.fromisoformat(str(row[0]))
            should_stop = False
            while not should_stop:
                try:
                    first = self._queue.get(timeout=self._batch_interval)
                except queue.Empty:
                    continue
                batch = [first]
                deadline = monotonic() + self._batch_interval
                while len(batch) < self._batch_size and monotonic() < deadline:
                    try:
                        batch.append(
                            self._queue.get(timeout=max(0.0, deadline - monotonic()))
                        )
                    except queue.Empty:
                        break
                started = perf_counter()
                committed = 0
                with connection:
                    for message in batch:
                        if isinstance(message, _StopMessage):
                            should_stop = True
                            continue
                        if isinstance(message, _CompletePhaseMessage):
                            if active_phase_id is not None:
                                connection.execute(
                                    "UPDATE phases SET ended_at=?, status='completed' WHERE id=?",
                                    (message.completed_at, active_phase_id),
                                )
                            active_phase_id = None
                            active_phase_name = None
                            phase_started = None
                            continue
                        if isinstance(message, _EventMessage):
                            connection.execute(
                                """
                                INSERT OR IGNORE INTO events (
                                    id, phase_id, recorded_at, severity, event_type,
                                    source, message, details_json, measurement_id
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                                """,
                                (
                                    message.event_id,
                                    active_phase_id,
                                    message.recorded_at,
                                    message.severity,
                                    message.event_type,
                                    message.source,
                                    message.message,
                                    message.details_json,
                                ),
                            )
                            committed += 1
                            continue
                        record = message.record
                        timestamp = record.snapshot.recorded_at.astimezone(UTC)
                        if project_started is None:
                            project_started = timestamp
                            connection.execute(
                                """
                                UPDATE project SET measurement_started_at=?, status='running'
                                """,
                                (timestamp.isoformat(),),
                            )
                        else:
                            connection.execute("UPDATE project SET status='running'")
                        if active_phase_id is None or active_phase_name != record.active_stage:
                            if active_phase_id is not None:
                                connection.execute(
                                    "UPDATE phases SET ended_at=?, status='completed' WHERE id=?",
                                    (timestamp.isoformat(), active_phase_id),
                                )
                            sequence = int(
                                connection.execute(
                                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM phases"
                                ).fetchone()[0]
                            )
                            cursor = connection.execute(
                                """
                                INSERT INTO phases (
                                    project_id, sequence, name, phase_type, started_at,
                                    status, settings_json, notes
                                ) SELECT id, ?, ?, 'measurement', ?, 'running', '{}', ''
                                  FROM project LIMIT 1
                                """,
                                (sequence, record.active_stage, timestamp.isoformat()),
                            )
                            if cursor.lastrowid is None:
                                raise ProjectDatabaseError("a fázisazonosító nem jött létre")
                            active_phase_id = int(cursor.lastrowid)
                            active_phase_name = record.active_stage
                            phase_started = timestamp
                        assert active_phase_id is not None
                        assert phase_started is not None
                        quality = record.snapshot.quality.value
                        pump_quality = quality
                        jacket_quality = (
                            pump_quality
                            if record.snapshot.jacket_pump.connected
                            else DataQuality.DISCONNECTED.value
                        )
                        injection_quality = (
                            pump_quality
                            if record.snapshot.injection_pump.connected
                            else DataQuality.DISCONNECTED.value
                        )
                        values = (
                            active_phase_id,
                            timestamp.isoformat(),
                            max(0.0, (timestamp - project_started).total_seconds()),
                            max(0.0, (timestamp - phase_started).total_seconds()),
                            record.snapshot.jacket_pump.pressure_bar,
                            record.snapshot.jacket_pump.flow_ml_per_hour / 60.0,
                            record.snapshot.jacket_pump.remaining_volume_ml,
                            record.jacket_net_volume_ml,
                            record.snapshot.injection_pump.pressure_bar,
                            record.snapshot.injection_pump.flow_ml_per_hour / 60.0,
                            record.snapshot.injection_pump.remaining_volume_ml,
                            record.injection_net_volume_ml,
                            record.snapshot.line_pressure_bar,
                            record.snapshot.differential_pressure_bar,
                            record.snapshot.valve_percent,
                            jacket_quality,
                            injection_quality,
                            quality,
                            quality,
                            None,
                            None,
                            None,
                            None,
                            "safe_stop" if record.safety_reasons else "normal",
                            json.dumps(record.safety_reasons, ensure_ascii=False),
                            record.active_stage,
                            record.snapshot.raw_line_voltage,
                            (
                                None
                                if record.snapshot.line_pressure_reading is None
                                else record.snapshot.line_pressure_reading.median_voltage
                            ),
                            (
                                None
                                if record.snapshot.line_pressure_reading is None
                                else record.snapshot.line_pressure_reading.filtered_voltage
                            ),
                            record.snapshot.raw_line_pressure_bar,
                            record.snapshot.line_pressure_quality.value,
                            record.snapshot.line_pressure_quality_reason,
                            record.snapshot.line_pressure_sample_age_seconds,
                            record.snapshot.raw_differential_voltage,
                            (
                                None
                                if record.snapshot.differential_pressure_reading is None
                                else record.snapshot.differential_pressure_reading.median_voltage
                            ),
                            (
                                None
                                if record.snapshot.differential_pressure_reading is None
                                else record.snapshot.differential_pressure_reading.filtered_voltage
                            ),
                            record.snapshot.raw_differential_pressure_bar,
                            record.snapshot.differential_pressure_quality.value,
                            record.snapshot.differential_pressure_quality_reason,
                            record.snapshot.differential_pressure_sample_age_seconds,
                        )
                        connection.execute(
                            """
                            INSERT INTO measurements (
                                phase_id, recorded_at, project_elapsed_s, phase_elapsed_s,
                                jacket_pressure_bar, jacket_flow_ml_min,
                                jacket_remaining_volume_ml, jacket_injected_volume_ml,
                                injection_pressure_bar, injection_flow_ml_min,
                                injection_remaining_volume_ml, injection_injected_volume_ml,
                                line_pressure_bar, differential_pressure_bar,
                                valve_position_percent, jacket_data_quality,
                                injection_data_quality, pressure_data_quality,
                                valve_data_quality, jacket_sample_age_s,
                                injection_sample_age_s, pressure_sample_age_s,
                                valve_sample_age_s, safety_state, diagnostic_flags,
                                control_state, raw_line_voltage, median_line_voltage,
                                filtered_line_voltage, raw_line_pressure_bar,
                                line_data_quality, line_quality_reason, line_sample_age_s,
                                raw_differential_voltage,
                                median_differential_voltage,
                                filtered_differential_voltage,
                                raw_differential_pressure_bar,
                                differential_data_quality,
                                differential_quality_reason,
                                differential_sample_age_s
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                            )
                            """,
                            values,
                        )
                        committed += 1
                transaction_seconds = perf_counter() - started
                with self._metrics_lock:
                    self._committed_count += committed
                    self._transaction_count += 1
                    self._transaction_total_seconds += transaction_seconds
                    self._transaction_max_seconds = max(
                        self._transaction_max_seconds, transaction_seconds
                    )
                for _ in batch:
                    self._queue.task_done()
            with connection:
                ended_at = datetime.now(UTC).isoformat()
                if active_phase_id is not None:
                    connection.execute(
                        "UPDATE phases SET ended_at=?, status='completed' WHERE id=?",
                        (ended_at, active_phase_id),
                    )
                connection.execute(
                    """
                    UPDATE project SET measurement_ended_at=?, status='completed'
                    WHERE measurement_started_at IS NOT NULL
                    """,
                    (ended_at,),
                )
        except BaseException as error:
            self._error = error
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
                else:
                    self._queue.task_done()
        finally:
            if connection is not None:
                connection.close()
            self._stopped.set()


def list_phases(path: Path) -> tuple[PhaseRow, ...]:
    connection = _connect(path, read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT id, sequence, name, phase_type, started_at, ended_at, status
            FROM phases ORDER BY sequence, id
            """
        ).fetchall()
        return tuple(
            PhaseRow(
                int(row["id"]),
                int(row["sequence"]),
                str(row["name"]),
                str(row["phase_type"]),
                str(row["started_at"]),
                None if row["ended_at"] is None else str(row["ended_at"]),
                str(row["status"]),
            )
            for row in rows
        )
    finally:
        connection.close()


def query_measurements(path: Path, query: MeasurementQuery) -> MeasurementRows:
    columns = query.columns or _MEASUREMENT_COLUMNS[:-1]
    invalid = set(columns) - _QUERYABLE_COLUMNS
    if invalid:
        raise ValueError(f"nem lekérdezhető mérési oszlop: {', '.join(sorted(invalid))}")
    if query.max_points is not None and query.max_points < 1:
        raise ValueError("a ritkított pontszámnak pozitívnak kell lennie")
    where: list[str] = []
    parameters: list[object] = []
    if query.phase_id is not None:
        where.append("phase_id = ?")
        parameters.append(query.phase_id)
    if query.start_at is not None:
        where.append("recorded_at >= ?")
        parameters.append(query.start_at)
    if query.end_at is not None:
        where.append("recorded_at <= ?")
        parameters.append(query.end_at)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    connection = _connect(path, read_only=True)
    try:
        stride = 1
        if query.max_points is not None:
            count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM measurements{where_sql}",  # noqa: S608
                    parameters,
                ).fetchone()[0]
            )
            stride = max(1, (count + query.max_points - 1) // query.max_points)
        selected = ", ".join(f'"{column}"' for column in columns)
        sql = f"SELECT {selected} FROM measurements{where_sql}"  # noqa: S608
        if stride > 1:
            condition = " AND" if where else " WHERE"
            sql += f"{condition} (id % ?) = 0"
            parameters.append(stride)
        sql += " ORDER BY recorded_at, id"
        if query.limit is not None:
            sql += " LIMIT ? OFFSET ?"
            parameters.extend((query.limit, query.offset))
        rows = connection.execute(sql, parameters).fetchall()
        return MeasurementRows(
            columns,
            tuple(tuple(row[column] for column in columns) for row in rows),
        )
    finally:
        connection.close()


def query_events(
    path: Path,
    *,
    phase_id: int | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
) -> tuple[sqlite3.Row, ...]:
    where: list[str] = []
    parameters: list[object] = []
    if phase_id is not None:
        where.append("phase_id = ?")
        parameters.append(phase_id)
    if start_at is not None:
        where.append("recorded_at >= ?")
        parameters.append(start_at)
    if end_at is not None:
        where.append("recorded_at <= ?")
        parameters.append(end_at)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    connection = _connect(path, read_only=True)
    try:
        return tuple(
            connection.execute(
                f"SELECT * FROM events{where_sql} ORDER BY recorded_at, id",  # noqa: S608
                parameters,
            ).fetchall()
        )
    finally:
        connection.close()


def create_database_snapshot(source: Path, destination: Path) -> Path:
    """Create a consistent standalone SQLite backup suitable for NAS copying."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{monotonic():.0f}.tmp")
    source_connection = _connect(source, read_only=True)
    destination_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(destination_connection)
        destination_connection.execute("PRAGMA journal_mode = DELETE")
        result = str(destination_connection.execute("PRAGMA quick_check").fetchone()[0])
        if result != "ok":
            raise ProjectDatabaseError(f"érvénytelen SQLite biztonsági másolat: {result}")
        destination_connection.close()
        source_connection.close()
        temporary.replace(destination)
        return destination
    except BaseException:
        destination_connection.close()
        source_connection.close()
        temporary.unlink(missing_ok=True)
        raise


def database_query_plan(path: Path, *, phase_id: int, start_at: str) -> tuple[str, ...]:
    connection = _connect(path, read_only=True)
    try:
        rows = connection.execute(
            """
            EXPLAIN QUERY PLAN SELECT recorded_at, jacket_pressure_bar
            FROM measurements WHERE phase_id = ? AND recorded_at >= ?
            ORDER BY recorded_at
            """,
            (phase_id, start_at),
        ).fetchall()
        return tuple(str(row[3]) for row in rows)
    finally:
        connection.close()


def write_migration_rows(
    path: Path,
    *,
    project_id: int,
    phase_name: str,
    phase_type: str,
    source_key: str,
    rows: Sequence[dict[str, object]],
) -> tuple[int, int]:
    """Insert normalized legacy rows idempotently; used by the CSV adapter."""
    connection = _connect(path)
    try:
        with connection:
            phase = connection.execute(
                "SELECT id, started_at FROM phases WHERE source_key = ?", (source_key,)
            ).fetchone()
            if phase is None:
                sequence = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM phases"
                    ).fetchone()[0]
                )
                started_at = str(rows[0]["recorded_at"]) if rows else datetime.now(UTC).isoformat()
                cursor = connection.execute(
                    """
                    INSERT INTO phases (
                        project_id, sequence, name, phase_type, started_at,
                        status, settings_json, notes, source_key
                    ) VALUES (?, ?, ?, ?, ?, 'migrated', '{}', '', ?)
                    """,
                    (project_id, sequence, phase_name, phase_type, started_at, source_key),
                )
                if cursor.lastrowid is None:
                    raise ProjectDatabaseError("a migrált fázisazonosító nem jött létre")
                phase_id = int(cursor.lastrowid)
                phase_started = datetime.fromisoformat(started_at)
            else:
                phase_id = int(phase[0])
                phase_started = datetime.fromisoformat(str(phase[1]))
            project_row = connection.execute(
                "SELECT measurement_started_at FROM project WHERE id = ?", (project_id,)
            ).fetchone()
            if project_row is None:
                raise ProjectDatabaseError(f"nem létező projekt: {project_id}")
            first_recorded = (
                datetime.fromisoformat(str(rows[0]["recorded_at"])) if rows else phase_started
            )
            if project_row[0] is None:
                project_started = first_recorded
                connection.execute(
                    "UPDATE project SET measurement_started_at=?, status='migrated' WHERE id=?",
                    (project_started.isoformat(), project_id),
                )
            else:
                project_started = datetime.fromisoformat(str(project_row[0]))
                if first_recorded < project_started:
                    elapsed_shift = (project_started - first_recorded).total_seconds()
                    connection.execute(
                        "UPDATE measurements SET project_elapsed_s=project_elapsed_s+?",
                        (elapsed_shift,),
                    )
                    project_started = first_recorded
                    connection.execute(
                        "UPDATE project SET measurement_started_at=? WHERE id=?",
                        (project_started.isoformat(), project_id),
                    )
            inserted = 0
            duplicates = 0
            for row_number, row in enumerate(rows, start=2):
                recorded_at = datetime.fromisoformat(str(row["recorded_at"]))
                item_source_key = f"{source_key}:{row_number}"
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO measurements (
                        phase_id, recorded_at, project_elapsed_s, phase_elapsed_s,
                        jacket_pressure_bar, jacket_flow_ml_min,
                        jacket_remaining_volume_ml, jacket_injected_volume_ml,
                        injection_pressure_bar, injection_flow_ml_min,
                        injection_remaining_volume_ml, injection_injected_volume_ml,
                        line_pressure_bar, differential_pressure_bar,
                        valve_position_percent, jacket_data_quality,
                        injection_data_quality, pressure_data_quality,
                        valve_data_quality, diagnostic_flags, control_state,
                        raw_line_voltage, median_line_voltage,
                        filtered_line_voltage, raw_line_pressure_bar,
                        line_data_quality, line_quality_reason, line_sample_age_s,
                        raw_differential_voltage, median_differential_voltage,
                        filtered_differential_voltage, raw_differential_pressure_bar,
                        differential_data_quality, differential_quality_reason,
                        differential_sample_age_s,
                        source_key
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        phase_id,
                        recorded_at.isoformat(),
                        max(0.0, (recorded_at - project_started).total_seconds()),
                        max(0.0, (recorded_at - phase_started).total_seconds()),
                        row.get("jacket_pressure_bar"),
                        _hour_to_minute(row.get("jacket_flow_ml_per_hour")),
                        row.get("jacket_remaining_volume_ml"),
                        row.get("jacket_net_volume_ml"),
                        row.get("injection_pressure_bar"),
                        _hour_to_minute(row.get("injection_flow_ml_per_hour")),
                        row.get("injection_remaining_volume_ml"),
                        row.get("injection_net_volume_ml"),
                        row.get("line_pressure_bar"),
                        row.get("differential_pressure_bar"),
                        row.get("valve_percent"),
                        row.get("quality"),
                        row.get("quality"),
                        row.get("quality"),
                        row.get("quality"),
                        phase_name,
                        row.get("raw_line_voltage"),
                        row.get("median_line_voltage"),
                        row.get("filtered_line_voltage"),
                        row.get("raw_line_pressure_bar"),
                        row.get("line_pressure_quality") or row.get("quality"),
                        row.get("line_pressure_quality_reason"),
                        row.get("line_pressure_sample_age_seconds"),
                        row.get("raw_differential_voltage"),
                        row.get("median_differential_voltage"),
                        row.get("filtered_differential_voltage"),
                        row.get("raw_differential_pressure_bar"),
                        row.get("differential_pressure_quality") or row.get("quality"),
                        row.get("differential_pressure_quality_reason"),
                        row.get("differential_pressure_sample_age_seconds"),
                        item_source_key,
                    ),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    duplicates += 1
            if rows:
                connection.execute(
                    "UPDATE phases SET ended_at=?, status='migrated' WHERE id=?",
                    (str(rows[-1]["recorded_at"]), phase_id),
                )
            return inserted, duplicates
    finally:
        connection.close()


def _hour_to_minute(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(str(value)) / 60.0
