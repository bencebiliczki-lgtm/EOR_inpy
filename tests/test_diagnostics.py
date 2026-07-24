import os
from pathlib import Path
from time import time

from eor_control.diagnostics import (
    STRUCTURED_EVENT_FIELDS,
    DiagnosticCategory,
    DiagnosticLogger,
    LogRetentionSettings,
)


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


def test_structured_event_contains_required_fields_and_context(tmp_path: Path) -> None:
    logger = DiagnosticLogger(tmp_path / "application.html")
    logger.configure(enabled=True, categories=DiagnosticCategory)
    logger.set_context_provider(
        lambda: {"measurement_id": "measurement-42", "section_id": 7}
    )

    logger.emit_event(
        DiagnosticCategory.JACKET_PUMP,
        "TELEMETRY_QUALITY_CHANGED",
        fields={
            "device": "jacket_pump",
            "field": "pressure",
            "previous_quality": "good",
            "new_quality": "stale",
            "age_ms": 2476,
            "stale_limit_ms": 2000,
        },
    )

    event = logger.events_after(0)[0]
    fields = dict(event.fields)
    assert event.event_id == "TELEMETRY_QUALITY_CHANGED"
    assert fields["measurement_id"] == "measurement-42"
    assert fields["section_id"] == "7"
    assert set(STRUCTURED_EVENT_FIELDS).issubset(fields)
    assert fields["event_id"] == "TELEMETRY_QUALITY_CHANGED"
    assert "timestamp" in fields
    assert "monotonic_seconds" in fields
    assert "previous_quality=good" in event.message
    assert "new_quality=stale" in event.message


def test_cleanup_only_deletes_expired_managed_inactive_unlocked_logs(
    tmp_path: Path,
) -> None:
    active = tmp_path / "application.html"
    hardware = tmp_path / "hardware_communication.html"
    logger = DiagnosticLogger(active, hardware_path=hardware)
    logger.configure(enabled=True, categories=DiagnosticCategory)
    logger.emit(DiagnosticCategory.SYSTEM, "STATE", "active")
    logger.emit(DiagnosticCategory.JACKET_PUMP, "RX", "active hardware")
    expired = tmp_path / "application-20260101-000000-deadbeef.html"
    expired.write_text("expired", encoding="utf-8")
    locked = tmp_path / "hardware_communication-20260101-000000-locked.html"
    locked.write_text("locked", encoding="utf-8")
    locked.with_name(locked.name + ".lock").write_text("", encoding="utf-8")
    raw_csv = tmp_path / "measurement-raw.csv"
    raw_csv.write_text("must remain", encoding="utf-8")
    active_measurement = tmp_path / "measurement-active.html"
    active_measurement.write_text("open measurement", encoding="utf-8")
    logger.set_protected_log_paths([active_measurement])
    old = time() - 40 * 86400
    os.utime(expired, (old, old))
    os.utime(locked, (old, old))
    os.utime(active_measurement, (old, old))
    logger.configure_retention(
        LogRetentionSettings(
            retention_days=30,
            compression_enabled=False,
        )
    )

    result = logger.cleanup_logs()

    assert result.deleted_files == 1
    assert not expired.exists()
    assert active.exists()
    assert hardware.exists()
    assert locked.exists()
    assert raw_csv.exists()
    assert active_measurement.exists()


def test_cleanup_compresses_closed_logs_and_reports_directory_size(
    tmp_path: Path,
) -> None:
    logger = DiagnosticLogger(tmp_path / "application.html")
    logger.configure(enabled=True, categories=DiagnosticCategory)
    closed = tmp_path / "application-20260724-100000-cafebabe.html"
    closed.write_text("closed log" * 100, encoding="utf-8")

    result = logger.cleanup_logs()

    assert result.compressed_files == 1
    assert not closed.exists()
    assert closed.with_suffix(".html.gz").exists()
    assert result.remaining_bytes == logger.directory_size_bytes


def test_active_log_rotates_before_append_when_size_limit_is_reached(
    tmp_path: Path,
) -> None:
    active = tmp_path / "application.html"
    active.write_bytes(b"x" * 1024 * 1024)
    logger = DiagnosticLogger(active)
    logger.configure(enabled=True, categories=DiagnosticCategory)
    logger.configure_retention(
        LogRetentionSettings(maximum_file_size_mb=1)
    )

    logger.emit(DiagnosticCategory.SYSTEM, "STATE", "after rotation")

    rotated = tuple(tmp_path.glob("application-*.html"))
    assert len(rotated) == 1
    assert rotated[0].stat().st_size == 1024 * 1024
    assert "after rotation" in active.read_text(encoding="utf-8")
