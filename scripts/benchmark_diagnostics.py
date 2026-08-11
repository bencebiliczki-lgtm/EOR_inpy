"""Measure asynchronous diagnostic enqueue and drain costs without hardware I/O."""

from argparse import ArgumentParser
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from eor_control.diagnostics import DiagnosticCategory, DiagnosticLogger


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--events", type=int, default=1000)
    args = parser.parse_args()
    if args.events < 1:
        parser.error("--events must be positive")

    with TemporaryDirectory(prefix="eor-diagnostic-benchmark-") as directory:
        logger = DiagnosticLogger(Path(directory) / "application.html")
        logger.configure(enabled=True, categories=DiagnosticCategory)
        started = monotonic()
        for index in range(args.events):
            logger.emit(DiagnosticCategory.SYSTEM, "BENCHMARK", f"event {index}")
        emitted = monotonic()
        logger.flush(timeout_seconds=30.0)
        completed = monotonic()
        metrics = logger.queue_metrics
        logger.close()

    print(f"events={args.events}")
    print(f"enqueue_seconds={emitted - started:.6f}")
    print(f"drain_total_seconds={completed - started:.6f}")
    print(f"events_per_enqueue_second={args.events / (emitted - started):.2f}")
    print(f"batches={metrics.batches}")
    print(f"file_opens={metrics.file_opens}")
    print(f"flushes={metrics.flushes}")
    print(f"maximum_queue_size={metrics.maximum_size}")
    print(f"coalesced_events={metrics.coalesced_events}")


if __name__ == "__main__":
    main()
