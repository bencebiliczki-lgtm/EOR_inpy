from dataclasses import dataclass
from enum import StrEnum


class PreflightStatus(StrEnum):
    PASSED = "Megfelelt"
    WARNING = "Figyelmeztetés"
    FAILED = "Hiba"


@dataclass(frozen=True, slots=True)
class PreflightItem:
    key: str
    label: str
    status: PreflightStatus
    detail: str
    remediation: str = ""


@dataclass(frozen=True, slots=True)
class PreflightReport:
    items: tuple[PreflightItem, ...]

    @property
    def can_start(self) -> bool:
        return all(item.status is not PreflightStatus.FAILED for item in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(item.status is PreflightStatus.WARNING for item in self.items)

    def for_key(self, key: str) -> PreflightItem | None:
        return next((item for item in self.items if item.key == key), None)
