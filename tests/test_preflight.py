from eor_control.preflight import PreflightItem, PreflightReport, PreflightStatus


def test_preflight_report_allows_only_non_failed_items() -> None:
    report = PreflightReport(
        (
            PreflightItem("project", "Projekt", PreflightStatus.PASSED, "OK"),
            PreflightItem(
                "storage",
                "Tárhely",
                PreflightStatus.WARNING,
                "Szimulációban nincs adatmentés.",
            ),
        )
    )

    assert report.can_start
    assert report.has_warnings
    assert report.for_key("project") is report.items[0]
    assert report.for_key("missing") is None


def test_preflight_report_blocks_any_failed_item() -> None:
    report = PreflightReport(
        (
            PreflightItem(
                "margin",
                "Nyomáskülönbség",
                PreflightStatus.FAILED,
                "19 bar",
                "Legalább 20 bar szükséges.",
            ),
        )
    )

    assert not report.can_start
    assert not report.has_warnings
