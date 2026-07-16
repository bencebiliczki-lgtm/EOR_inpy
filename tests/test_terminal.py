from io import StringIO

from eor_control.terminal import TerminalApplication, run_terminal


def test_terminal_lifecycle_keeps_state_and_stops_safely() -> None:
    output = StringIO()
    terminal = TerminalApplication(output)
    try:
        assert terminal.execute("connect")
        assert terminal.snapshot().state == "ready"
        assert terminal.execute('set stage "Olajkiszorítás"')
        assert terminal.execute("set manual 25")
        assert terminal.execute("start")
        assert terminal.snapshot().state == "running"
        assert terminal.execute("stop")
        assert terminal.snapshot().state == "ready"
        assert terminal.execute("disconnect")
        assert terminal.snapshot().state == "idle"
    finally:
        terminal.close()
    assert "mérés elindult; adatmentés nincs" in output.getvalue()


def test_terminal_emergency_stop_requires_acknowledgement() -> None:
    output = StringIO()
    terminal = TerminalApplication(output)
    try:
        terminal.execute("connect")
        terminal.execute("start")
        terminal.execute("emergency-stop kezelői próba")
        snapshot = terminal.snapshot()
        assert snapshot.state == "fault"
        assert snapshot.fault_reason == "kezelői próba"
        terminal.execute("acknowledge")
        assert terminal.snapshot().state == "idle"
    finally:
        terminal.close()


def test_terminal_rejects_invalid_control_settings() -> None:
    output = StringIO()
    terminal = TerminalApplication(output)
    try:
        terminal.execute("set manual 101")
        terminal.execute("set automatic unknown 20")
    finally:
        terminal.close()
    messages = output.getvalue()
    assert "HIBA:" in messages
    assert "injection vagy line" in messages


def test_run_terminal_accepts_a_scripted_session() -> None:
    input_stream = StringIO("status\nconnect\nstart\nstop\ndisconnect\nexit\n")
    output_stream = StringIO()
    assert run_terminal(input_stream, output_stream) == 0
    output = output_stream.getvalue()
    assert "AFKI EOR terminál" in output
    assert "Állapot=IDLE" in output
    assert "mérés elindult" in output
