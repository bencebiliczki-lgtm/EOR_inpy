from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from time import monotonic


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
    def __init__(self, path: Path, *, capacity: int = 5000) -> None:
        if capacity < 1:
            raise ValueError("diagnostic capacity must be positive")
        self._path = path
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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"{event.recorded_at.isoformat()}\t{event.monotonic_seconds:.6f}\t"
            f"{event.level}\t{event.category.value}\t{event.direction}\t{event.message}\n"
        )
        with self._path.open("a", encoding="utf-8", newline="") as file:
            file.write(line)
            file.flush()
