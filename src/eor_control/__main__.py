import argparse
import ctypes
import sys
from collections.abc import Sequence


def _hide_private_windows_console() -> None:
    """Hide only a console created for a double-clicked GUI launch."""
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        process_ids = (ctypes.c_ulong * 4)()
        process_count = kernel32.GetConsoleProcessList(process_ids, len(process_ids))
        console_window = kernel32.GetConsoleWindow()
        private_frozen_console = bool(getattr(sys, "frozen", False)) and (
            0 < process_count <= 2
        )
        if private_frozen_console and console_window:
            ctypes.WinDLL("user32", use_last_error=True).ShowWindow(console_window, 0)
    except (AttributeError, OSError):
        return


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="AFKI-EOR")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("gui", "terminal"),
        default="gui",
        help="gui: grafikus felület; terminal: interaktív szimulációs vezérlés",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    if arguments.mode == "terminal":
        from eor_control.terminal import run_terminal

        raise SystemExit(run_terminal(sys.stdin, sys.stdout))
    _hide_private_windows_console()
    from eor_control.ui import run_ui

    raise SystemExit(run_ui())


if __name__ == "__main__":
    main()
