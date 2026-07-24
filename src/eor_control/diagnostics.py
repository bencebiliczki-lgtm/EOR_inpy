import gzip
import os
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from html import escape
from pathlib import Path
from threading import Lock, Thread
from time import monotonic
from uuid import uuid4

from eor_control.timezone import as_hungarian_time


class DiagnosticCategory(StrEnum):
    SYSTEM = "system"
    JACKET_PUMP = "jacket_pump"
    INJECTION_PUMP = "injection_pump"
    NI_LINE = "ni_line"
    NI_DIFFERENTIAL = "ni_differential"
    NI_VALVE = "ni_valve"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    sequence: int
    recorded_at: datetime
    monotonic_seconds: float
    category: DiagnosticCategory
    direction: str
    level: str
    message: str
    event_id: str = "DIAGNOSTIC"
    fields: tuple[tuple[str, str], ...] = ()


STRUCTURED_EVENT_FIELDS = (
    "measurement_id",
    "section_id",
    "device",
    "field",
    "previous_quality",
    "new_quality",
    "age_ms",
    "stale_limit_ms",
    "last_success_timestamp",
    "last_command",
    "last_command_elapsed_ms",
    "safety_rule",
    "selected_fault_strategy",
    "action",
    "action_result",
)


@dataclass(frozen=True, slots=True)
class LogRetentionSettings:
    retention_days: int = 30
    measurement_retention_days: int = 30
    maximum_file_size_mb: int = 25
    maximum_rotated_files: int = 30
    total_storage_limit_mb: int = 1024
    compression_enabled: bool = True
    automatic_cleanup_enabled: bool = True

    def __post_init__(self) -> None:
        values = (
            self.retention_days,
            self.measurement_retention_days,
            self.maximum_file_size_mb,
            self.maximum_rotated_files,
            self.total_storage_limit_mb,
        )
        if any(value < 1 for value in values):
            raise ValueError("log retention values must be positive")


@dataclass(frozen=True, slots=True)
class LogMaintenanceResult:
    completed_at: datetime
    deleted_files: int
    compressed_files: int
    reclaimed_bytes: int
    remaining_bytes: int
    warnings: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        return (
            f"deleted={self.deleted_files}; compressed={self.compressed_files}; "
            f"reclaimed_bytes={self.reclaimed_bytes}; "
            f"remaining_bytes={self.remaining_bytes}; warnings={len(self.warnings)}"
        )


class DiagnosticLogger:
    HARDWARE_CATEGORIES = frozenset(
        {
            DiagnosticCategory.JACKET_PUMP,
            DiagnosticCategory.INJECTION_PUMP,
            DiagnosticCategory.NI_LINE,
            DiagnosticCategory.NI_DIFFERENTIAL,
            DiagnosticCategory.NI_VALVE,
        }
    )
    _HTML_SUFFIX = """</tbody>
      </table>
    </main>
    <script>
      const search = document.getElementById("search");
      const level = document.getElementById("level");
      const counter = document.getElementById("counter");
      function filterRows() {
        const query = search.value.toLocaleLowerCase("hu");
        let visible = 0;
        const rows = document.querySelectorAll("tbody tr.log-event");
        for (const row of rows) {
          const matchesText = row.textContent.toLocaleLowerCase("hu").includes(query);
          const matchesLevel = !level.value || row.dataset.level === level.value;
          row.hidden = !(matchesText && matchesLevel);
          if (!row.hidden) visible += 1;
        }
        counter.textContent = `${visible} / ${rows.length} esemény`;
      }
      search.addEventListener("input", filterRows);
      level.addEventListener("change", filterRows);
      filterRows();
    </script>
  </body>
</html>
"""

    def __init__(
        self,
        path: Path,
        *,
        hardware_path: Path | None = None,
        capacity: int = 5000,
    ) -> None:
        if capacity < 1:
            raise ValueError("diagnostic capacity must be positive")
        self._path = path
        self._hardware_path = hardware_path or path
        self._events: deque[DiagnosticEvent] = deque(maxlen=capacity)
        self._enabled = False
        self._categories = set(DiagnosticCategory)
        self._sequence = 0
        self._lock = Lock()
        self._context_provider: Callable[[], Mapping[str, object]] | None = None
        self._retention = LogRetentionSettings()
        self._active_log_dates: dict[Path, str] = {}
        self._protected_log_paths: set[Path] = set()
        self._last_maintenance: LogMaintenanceResult | None = None
        self._maintenance_thread: Thread | None = None

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def path(self) -> Path:
        return self._path

    @property
    def hardware_path(self) -> Path:
        return self._hardware_path

    @property
    def categories(self) -> frozenset[DiagnosticCategory]:
        with self._lock:
            return frozenset(self._categories)

    @property
    def retention_settings(self) -> LogRetentionSettings:
        with self._lock:
            return self._retention

    @property
    def last_maintenance(self) -> LogMaintenanceResult | None:
        with self._lock:
            return self._last_maintenance

    @property
    def directory_size_bytes(self) -> int:
        directory = self._path.parent
        try:
            return sum(
                path.stat().st_size
                for path in directory.iterdir()
                if path.is_file() and self._is_managed_log(path)
            )
        except OSError:
            return 0

    def set_context_provider(
        self, provider: Callable[[], Mapping[str, object]] | None
    ) -> None:
        with self._lock:
            self._context_provider = provider

    def configure_retention(self, settings: LogRetentionSettings) -> None:
        with self._lock:
            self._retention = settings

    def set_protected_log_paths(self, paths: Iterable[Path]) -> None:
        with self._lock:
            self._protected_log_paths = {path.resolve() for path in paths}

    def configure(
        self, *, enabled: bool, categories: Iterable[DiagnosticCategory]
    ) -> None:
        with self._lock:
            self._enabled = enabled
            self._categories = set(categories)

    def emit(
        self,
        category: DiagnosticCategory,
        direction: str,
        message: str,
        *,
        level: str = "INFO",
    ) -> None:
        with self._lock:
            if not self._enabled or category not in self._categories:
                return
            self._sequence += 1
            event = DiagnosticEvent(
                sequence=self._sequence,
                recorded_at=datetime.now(UTC),
                monotonic_seconds=monotonic(),
                category=category,
                direction=direction,
                level=level,
                message=message.replace("\r", "[CR]").replace("\n", "[LF]"),
            )
            self._events.append(event)
            self._append_file(event)

    def emit_event(
        self,
        category: DiagnosticCategory,
        event_id: str,
        *,
        fields: Mapping[str, object] | None = None,
        direction: str = "EVENT",
        level: str = "INFO",
    ) -> None:
        provider = self._context_provider
        context: dict[str, object] = {}
        if provider is not None:
            try:
                context.update(provider())
            except Exception as error:
                context["context_error"] = f"{type(error).__name__}: {error}"
        if fields is not None:
            context.update(fields)
        normalized = {
            key: self._field_text(context.get(key))
            for key in STRUCTURED_EVENT_FIELDS
        }
        normalized.update(
            {
                str(key): self._field_text(value)
                for key, value in context.items()
                if key not in normalized
            }
        )
        message = " | ".join(
            [event_id, *(f"{key}={value}" for key, value in normalized.items())]
        )
        with self._lock:
            if not self._enabled or category not in self._categories:
                return
            self._sequence += 1
            recorded_at = datetime.now(UTC)
            monotonic_seconds = monotonic()
            event_fields = (
                ("timestamp", as_hungarian_time(recorded_at).isoformat()),
                ("event_id", event_id),
                ("monotonic_seconds", f"{monotonic_seconds:.6f}"),
                *tuple(normalized.items()),
            )
            event = DiagnosticEvent(
                sequence=self._sequence,
                recorded_at=recorded_at,
                monotonic_seconds=monotonic_seconds,
                category=category,
                direction=direction,
                level=level,
                message=message,
                event_id=event_id,
                fields=event_fields,
            )
            self._events.append(event)
            self._append_file(event)

    def cleanup_logs(self) -> LogMaintenanceResult:
        with self._lock:
            settings = self._retention
        result = self._perform_cleanup(settings)
        with self._lock:
            self._last_maintenance = result
        if result.deleted_files or result.compressed_files or result.warnings:
            self.emit_event(
                DiagnosticCategory.SYSTEM,
                "LOG_CLEANUP_COMPLETED",
                fields={
                    "action": "log_cleanup",
                    "action_result": (
                        "WARNING" if result.warnings else "SUCCESS"
                    ),
                    "deleted_files": result.deleted_files,
                    "compressed_files": result.compressed_files,
                    "reclaimed_bytes": result.reclaimed_bytes,
                    "remaining_bytes": result.remaining_bytes,
                    "warnings": "; ".join(result.warnings) or "NONE",
                },
                level="WARNING" if result.warnings else "INFO",
            )
        final_result = replace(
            result,
            remaining_bytes=self.directory_size_bytes,
        )
        with self._lock:
            self._last_maintenance = final_result
        return final_result

    def cleanup_logs_async(
        self, callback: Callable[[LogMaintenanceResult], None] | None = None
    ) -> bool:
        with self._lock:
            if self._maintenance_thread is not None and self._maintenance_thread.is_alive():
                return False

            def run() -> None:
                result = self.cleanup_logs()
                if callback is not None:
                    callback(result)

            self._maintenance_thread = Thread(
                target=run,
                name="eor-log-maintenance",
                daemon=True,
            )
            self._maintenance_thread.start()
            return True

    def events_after(self, sequence: int) -> tuple[DiagnosticEvent, ...]:
        with self._lock:
            return tuple(event for event in self._events if event.sequence > sequence)

    def clear_memory(self) -> None:
        with self._lock:
            self._events.clear()

    def _append_file(self, event: DiagnosticEvent) -> None:
        path = (
            self._hardware_path
            if event.category in self.HARDWARE_CATEGORIES
            else self._path
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_required(path, event.recorded_at)
        title = (
            "EOR diagnosztikai napló"
            if self._hardware_path == self._path
            else (
                "EOR hardverkommunikációs napló"
                if event.category in self.HARDWARE_CATEGORIES
                else "EOR alkalmazásnapló"
            )
        )
        self._ensure_html_document(path, title)
        row = self._html_row(event).encode("utf-8")
        suffix = self._HTML_SUFFIX.encode("utf-8")
        with path.open("r+b") as file:
            file.seek(-len(suffix), 2)
            file.write(row)
            file.write(suffix)
            file.truncate()
            file.flush()

    def _rotate_if_required(self, path: Path, recorded_at: datetime) -> None:
        date_key = recorded_at.strftime("%Y%m%d")
        existing_date = (
            datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y%m%d")
            if path.exists()
            else date_key
        )
        previous_date = self._active_log_dates.setdefault(path, existing_date)
        size_limit = self._retention.maximum_file_size_mb * 1024 * 1024
        too_large = path.exists() and path.stat().st_size >= size_limit
        new_day = path.exists() and previous_date != date_key
        if not too_large and not new_day:
            return
        timestamp = recorded_at.strftime("%Y%m%d-%H%M%S")
        rotated = path.with_name(
            f"{path.stem}-{timestamp}-{uuid4().hex[:8]}{path.suffix}"
        )
        path.replace(rotated)
        self._active_log_dates[path] = date_key

    def _perform_cleanup(
        self, settings: LogRetentionSettings
    ) -> LogMaintenanceResult:
        now = datetime.now(UTC)
        directory = self._path.parent
        with self._lock:
            protected = set(self._protected_log_paths)
        active_paths = {
            self._path.resolve(),
            self._hardware_path.resolve(),
            *protected,
        }
        warnings: list[str] = []
        deleted = 0
        compressed = 0
        reclaimed = 0
        try:
            candidates = [
                path
                for path in directory.iterdir()
                if path.is_file()
                and self._is_managed_log(path)
                and path.resolve() not in active_paths
                and not self._is_locked(path)
            ]
        except OSError as error:
            return LogMaintenanceResult(
                completed_at=now,
                deleted_files=0,
                compressed_files=0,
                reclaimed_bytes=0,
                remaining_bytes=self.directory_size_bytes,
                warnings=(f"log directory scan failed: {error}",),
            )

        if settings.compression_enabled:
            for path in tuple(candidates):
                if path.suffix.lower() != ".html":
                    continue
                target = path.with_suffix(path.suffix + ".gz")
                try:
                    source_stat = path.stat()
                    before = source_stat.st_size
                    with path.open("rb") as source, gzip.open(target, "wb") as destination:
                        while chunk := source.read(1024 * 1024):
                            destination.write(chunk)
                    os.utime(target, (source_stat.st_atime, source_stat.st_mtime))
                    path.unlink()
                    after = target.stat().st_size
                    reclaimed += max(0, before - after)
                    compressed += 1
                    candidates.remove(path)
                    candidates.append(target)
                except OSError as error:
                    target.unlink(missing_ok=True)
                    warnings.append(f"{path.name} compression failed: {error}")

        candidates.sort(key=self._safe_mtime)
        keep_after = now.timestamp() - settings.retention_days * 86400
        measurement_keep_after = (
            now.timestamp() - settings.measurement_retention_days * 86400
        )
        for path in tuple(candidates):
            cutoff = (
                measurement_keep_after
                if path.name.startswith("measurement-")
                else keep_after
            )
            if self._safe_mtime(path) >= cutoff:
                continue
            removed = self._delete_managed_log(path, warnings)
            if removed is not None:
                deleted += 1
                reclaimed += removed
                candidates.remove(path)

        grouped: dict[str, list[Path]] = {}
        for path in candidates:
            group = (
                "application"
                if path.name.startswith("application-")
                else (
                    "hardware_communication"
                    if path.name.startswith("hardware_communication-")
                    else "measurement"
                )
            )
            grouped.setdefault(group, []).append(path)
        for paths in grouped.values():
            paths.sort(key=self._safe_mtime, reverse=True)
            for path in paths[settings.maximum_rotated_files :]:
                removed = self._delete_managed_log(path, warnings)
                if removed is not None:
                    deleted += 1
                    reclaimed += removed
                    if path in candidates:
                        candidates.remove(path)

        storage_limit = settings.total_storage_limit_mb * 1024 * 1024
        candidates.sort(key=self._safe_mtime)
        total = self.directory_size_bytes
        for path in candidates:
            if total <= storage_limit:
                break
            removed = self._delete_managed_log(path, warnings)
            if removed is not None:
                deleted += 1
                reclaimed += removed
                total -= removed

        return LogMaintenanceResult(
            completed_at=now,
            deleted_files=deleted,
            compressed_files=compressed,
            reclaimed_bytes=reclaimed,
            remaining_bytes=self.directory_size_bytes,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _field_text(value: object) -> str:
        if value is None or value == "":
            return "NONE"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).replace("\r", "[CR]").replace("\n", "[LF]")

    @staticmethod
    def _safe_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _is_locked(path: Path) -> bool:
        return ".locked." in path.name or path.with_name(path.name + ".lock").exists()

    @staticmethod
    def _is_managed_log(path: Path) -> bool:
        name = path.name
        valid_suffix = name.endswith(".html") or name.endswith(".html.gz")
        if not valid_suffix:
            return False
        return (
            name in {"application.html", "hardware_communication.html"}
            or name.startswith("application-")
            or name.startswith("hardware_communication-")
            or name.startswith("measurement-")
        )

    @classmethod
    def _delete_managed_log(
        cls, path: Path, warnings: list[str]
    ) -> int | None:
        if not cls._is_managed_log(path) or cls._is_locked(path):
            return None
        try:
            size = path.stat().st_size
            path.unlink()
            return size
        except OSError as error:
            warnings.append(f"{path.name} deletion failed: {error}")
            return None

    def _ensure_html_document(self, path: Path, title: str) -> None:
        suffix = self._HTML_SUFFIX.encode("utf-8")
        if not path.exists() or path.stat().st_size == 0:
            path.write_text(
                self._html_prefix(title) + self._HTML_SUFFIX,
                encoding="utf-8",
                newline="",
            )
            return
        with path.open("rb") as file:
            if path.stat().st_size >= len(suffix):
                file.seek(-len(suffix), 2)
                if file.read() == suffix:
                    return
        previous = path.read_text(encoding="utf-8", errors="replace")
        preserved = (
            '<tr class="legacy"><td colspan="6"><strong>Korábbi naplótartalom</strong>'
            f"<pre>{escape(previous)}</pre></td></tr>\n"
        )
        path.write_text(
            self._html_prefix(title) + preserved + self._HTML_SUFFIX,
            encoding="utf-8",
            newline="",
        )

    @staticmethod
    def _html_prefix(title: str) -> str:
        safe_title = escape(title)
        return f"""<!doctype html>
<html lang="hu">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{safe_title}</title>
    <style>
      :root {{ color-scheme: light dark; font-family: "Segoe UI", Arial, sans-serif; }}
      body {{ margin: 0; background: #f4f7fb; color: #17212b; }}
      header {{ padding: 22px 28px; color: white; background: #173b57; }}
      h1 {{ margin: 0 0 6px; font-size: 24px; }}
      header p {{ margin: 0; color: #d9e8f2; }}
      main {{ padding: 20px 28px 32px; }}
      .toolbar {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
                  margin-bottom: 14px; }}
      input, select {{ min-height: 36px; padding: 0 10px; border: 1px solid #aebdca;
                       border-radius: 6px; background: white; color: #17212b; }}
      input {{ flex: 1 1 320px; }}
      #counter {{ margin-left: auto; font-weight: 700; }}
      .table-wrap {{ overflow: auto; max-height: calc(100vh - 190px); border-radius: 8px;
                     border: 1px solid #cbd6df; background: white; }}
      table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
      th {{ position: sticky; top: 0; z-index: 1; padding: 10px; text-align: left;
            color: white; background: #285978; white-space: nowrap; }}
      td {{ padding: 8px 10px; border-bottom: 1px solid #e1e7ec; vertical-align: top; }}
      tbody tr:nth-child(even) {{ background: #f7fafc; }}
      tbody tr:hover {{ background: #eaf3f8; }}
      td.message {{ min-width: 340px; white-space: pre-wrap; overflow-wrap: anywhere; }}
      .level-warning td {{ background: #fff4cf; }}
      .level-error td, .level-critical td {{ background: #ffe2e2; color: #8b1111; }}
      .badge {{ display: inline-block; padding: 2px 7px; border-radius: 999px;
                background: #dceaf3; font-weight: 700; white-space: nowrap; }}
      .level-warning .badge {{ background: #ffd970; color: #513900; }}
      .level-error .badge, .level-critical .badge {{ background: #d82c3b; color: white; }}
      tr.legacy pre {{ max-height: 240px; overflow: auto; white-space: pre-wrap; }}
      @media (prefers-color-scheme: dark) {{
        body {{ background: #111820; color: #e5edf3; }}
        input, select, .table-wrap {{ background: #18232d; color: #e5edf3;
                                     border-color: #425564; }}
        td {{ border-color: #334450; }}
        tbody tr:nth-child(even) {{ background: #1b2832; }}
        tbody tr:hover {{ background: #263b49; }}
        .level-warning td {{ background: #493d19; color: #ffeaa1; }}
        .level-error td, .level-critical td {{ background: #4b2024; color: #ffc9ce; }}
      }}
    </style>
  </head>
  <body>
    <header>
      <h1>{safe_title}</h1>
      <p>AFKI EOR mérőrendszer — Europe/Budapest megjelenítés,<br>
        UTC tárolás és monotonic időreferencia</p>
    </header>
    <main>
      <div class="toolbar">
        <input id="search" type="search"
               placeholder="Keresés kategória, irány vagy üzenet alapján…">
        <label for="level">Szint:</label>
        <select id="level">
          <option value="">Mind</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>
        <span id="counter">0 esemény</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Magyar idő</th><th>Monotonic</th><th>Szint</th>
            <th>Kategória</th><th>Irány</th><th>Üzenet</th>
          </tr></thead>
          <tbody>
"""

    @staticmethod
    def _html_row(event: DiagnosticEvent) -> str:
        level = event.level.strip().upper() or "INFO"
        css_level = "".join(character for character in level.lower() if character.isalnum())
        utc_timestamp = event.recorded_at.isoformat()
        values = (
            as_hungarian_time(event.recorded_at).isoformat(),
            f"{event.monotonic_seconds:.6f}",
            level,
            event.category.value,
            event.direction,
            event.message,
        )
        escaped = tuple(escape(value) for value in values)
        return (
            f'<tr class="log-event level-{css_level}" data-level="{escaped[2]}" '
            f'data-category="{escaped[3]}">'
            f'<td><time datetime="{escape(utc_timestamp)}">{escaped[0]}</time></td>'
            f'<td>{escaped[1]}</td><td><span class="badge">{escaped[2]}</span></td>'
            f'<td>{escaped[3]}</td><td>{escaped[4]}</td>'
            f'<td class="message">{escaped[5]}</td></tr>\n'
        )
