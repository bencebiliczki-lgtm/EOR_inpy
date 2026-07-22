from pathlib import Path

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger


def test_disabled_logger_does_not_record_or_create_file(tmp_path: Path) -> None:
    path = tmp_path / "communication.html"
    logger = DiagnosticLogger(path)

    assert logger.path == path
    assert logger.hardware_path == path
    logger.emit(DiagnosticCategory.JACKET_PUMP, "TX", "RSVP")

    assert logger.events_after(0) == ()
    assert not path.exists()


def test_logger_filters_categories_and_appends_file(tmp_path: Path) -> None:
    path = tmp_path / "communication.html"
    logger = DiagnosticLogger(path)
    logger.configure(enabled=True, categories=[DiagnosticCategory.JACKET_PUMP])

    logger.emit(DiagnosticCategory.NI_LINE, "RX", "2.0 V")
    logger.emit(DiagnosticCategory.JACKET_PUMP, "TX", "6R <58>&\r")

    events = logger.events_after(0)
    assert len(events) == 1
    assert events[0].category is DiagnosticCategory.JACKET_PUMP
    assert events[0].message == "6R <58>&[CR]"
    report = path.read_text(encoding="utf-8")
    assert report.startswith("<!doctype html>")
    assert '<tr class="log-event level-info"' in report
    assert 'data-category="jacket_pump"' in report
    assert "6R &lt;58&gt;&amp;[CR]" in report
    assert "6R <58>" not in report
    assert report.endswith("</html>\n")
    assert 'id="search"' in report
    assert 'id="level"' in report
    assert "Europe/Budapest megjelenítés" in report
    assert "Magyar idő" in report


def test_events_can_be_read_incrementally_and_memory_cleared(tmp_path: Path) -> None:
    logger = DiagnosticLogger(tmp_path / "communication.html")
    logger.configure(enabled=True, categories=DiagnosticCategory)
    logger.emit(DiagnosticCategory.SYSTEM, "STATE", "first")
    first_sequence = logger.events_after(0)[0].sequence
    logger.emit(DiagnosticCategory.RUNTIME, "STATE", "second")

    assert [event.message for event in logger.events_after(first_sequence)] == ["second"]
    logger.clear_memory()
    assert logger.events_after(0) == ()


def test_hardware_events_are_written_to_separate_file(tmp_path: Path) -> None:
    application_path = tmp_path / "application.html"
    hardware_path = tmp_path / "hardware_communication.html"
    logger = DiagnosticLogger(application_path, hardware_path=hardware_path)
    logger.configure(enabled=True, categories=DiagnosticCategory)

    logger.emit(DiagnosticCategory.SYSTEM, "DISCOVERY", "inventory complete")
    logger.emit(DiagnosticCategory.JACKET_PUMP, "TX", "RSVP")
    logger.emit(DiagnosticCategory.NI_LINE, "RX", "2.0 V")

    application_log = application_path.read_text(encoding="utf-8")
    assert "EOR alkalmazásnapló" in application_log
    assert "inventory complete" in application_log
    hardware_log = hardware_path.read_text(encoding="utf-8")
    assert "EOR hardverkommunikációs napló" in hardware_log
    assert 'data-category="jacket_pump"' in hardware_log
    assert ">RSVP</td>" in hardware_log
    assert 'data-category="ni_line"' in hardware_log
    assert ">2.0 V</td>" in hardware_log
    assert "inventory complete" not in hardware_log
    assert hardware_log.count('class="log-event') == 2
