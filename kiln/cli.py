"""kiln command-line interface.

Subcommands:
  * ``kiln run <folder>``  — manually enqueue a job folder (master + job.json).
  * ``kiln status``        — show the processing queue, pending-archive queue, recent jobs.
  * ``kiln doctor``        — print detected GPU/VRAM/NVENC generation + AV1 support, and
                             verify that ``$INBOX`` is writable and ``$ARCHIVE`` is
                             reachable/writable. The one-command "is my setup correct?".
"""

from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kiln", description="GPU video post-processing pipeline")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="enqueue a job folder")
    run.add_argument("folder", help="path to a job folder (master + job.json)")

    sub.add_parser("status", help="show queues and recent jobs")
    sub.add_parser("doctor", help="print hardware detection and storage checks")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point (see ``[project.scripts]`` in pyproject.toml)."""
    args = _build_parser().parse_args(argv)
    # Command implementations land in Phase 2. Fail loudly rather than pretend success.
    raise NotImplementedError(f"kiln {args.command} is implemented in Phase 2 (engine core)")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
