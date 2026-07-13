from pathlib import Path

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger


def test_disabled_logger_does_not_record_or_create_file(tmp_path: Path) -> None:
    path = tmp_path / "communication.log"
    logger = DiagnosticLogger(path)

    logger.emit(DiagnosticCategory.JACKET_PUMP, "TX", "RSVP")

    assert logger.events_after(0) == ()
    assert not path.exists()


def test_logger_filters_categories_and_appends_file(tmp_path: Path) -> None:
    path = tmp_path / "communication.log"
    logger = DiagnosticLogger(path)
    logger.configure(enabled=True, categories=[DiagnosticCategory.JACKET_PUMP])

    logger.emit(DiagnosticCategory.NI_LINE, "RX", "2.0 V")
    logger.emit(DiagnosticCategory.JACKET_PUMP, "TX", "6R 58\r")

    events = logger.events_after(0)
    assert len(events) == 1
    assert events[0].category is DiagnosticCategory.JACKET_PUMP
    assert events[0].message == "6R 58[CR]"
    assert "jacket_pump\tTX\t6R 58[CR]" in path.read_text(encoding="utf-8")


def test_events_can_be_read_incrementally_and_memory_cleared(tmp_path: Path) -> None:
    logger = DiagnosticLogger(tmp_path / "communication.log")
    logger.configure(enabled=True, categories=DiagnosticCategory)
    logger.emit(DiagnosticCategory.SYSTEM, "STATE", "first")
    first_sequence = logger.events_after(0)[0].sequence
    logger.emit(DiagnosticCategory.RUNTIME, "STATE", "second")

    assert [event.message for event in logger.events_after(first_sequence)] == ["second"]
    logger.clear_memory()
    assert logger.events_after(0) == ()
