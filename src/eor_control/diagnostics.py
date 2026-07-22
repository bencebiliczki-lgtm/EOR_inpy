from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from html import escape
from pathlib import Path
from threading import Lock
from time import monotonic

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
