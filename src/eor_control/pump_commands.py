from dataclasses import dataclass
from enum import IntEnum, StrEnum
from math import isfinite


class PumpCommandPriority(IntEnum):
    EMERGENCY = 0
    HIGH = 1
    NORMAL = 2


class PumpCommandKind(StrEnum):
    ENTER_REMOTE = "REMOTE"
    SET_PRESSURE_LIMIT = "MAXPRESS"
    SET_CONSTANT_FLOW = "CONST_FLOW"
    SET_CONSTANT_PRESSURE = "CONST_PRESS"
    READ_CONFIGURED_FLOW = "SETFLOW_READBACK"
    RUN = "RUN"
    STOP = "STOP"
    CLEAR = "CLEAR"
    RETURN_LOCAL = "LOCAL"


class PumpCommandStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {
            PumpCommandStatus.SUCCEEDED,
            PumpCommandStatus.FAILED,
            PumpCommandStatus.TIMED_OUT,
            PumpCommandStatus.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class PumpCommand:
    kind: PumpCommandKind
    priority: PumpCommandPriority
    value: float | None = None
    timeout_seconds: float = 5.0
    verify_status: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0.0:
            raise ValueError("pump command timeout must be positive and finite")
        if self.value is not None and not isfinite(self.value):
            raise ValueError("pump command value must be finite")


@dataclass(frozen=True, slots=True)
class PumpCommandResult:
    command_id: str
    command: PumpCommand
    status: PumpCommandStatus
    submitted_monotonic: float
    started_monotonic: float | None = None
    completed_monotonic: float | None = None
    value: float | None = None
    operating_status: str | None = None
    error: str | None = None

    @property
    def queue_wait_seconds(self) -> float | None:
        if self.started_monotonic is None:
            return None
        return self.started_monotonic - self.submitted_monotonic

    @property
    def transaction_seconds(self) -> float | None:
        if self.started_monotonic is None or self.completed_monotonic is None:
            return None
        return self.completed_monotonic - self.started_monotonic
