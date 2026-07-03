"""kiln command-line interface.

Subcommands:
  * ``kiln run <folder>``  — manually enqueue a job folder (master + job.json).
  * ``kiln status``        — show the processing queue, pending-archive queue, recent jobs.
  * ``kiln serve``         — run the service loop (``--once`` for a single pass).
  * ``kiln doctor``        — print detected GPU/VRAM/NVENC generation + AV1 support, and
                             verify that ``$INBOX`` is writable and both ``$MASTERS_ARCHIVE``
                             and ``$EXPORTS_ARCHIVE`` are reachable/writable. The one-command
                             "is my setup correct?".
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from kiln import config as config_mod
from kiln.config import Config
from kiln.paths import PENDING, StateLayout
from kiln.queue import ProcessingQueue


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kiln", description="GPU video post-processing pipeline")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="enqueue a job folder")
    run.add_argument("folder", help="path to a job folder (master + job.json)")

    sub.add_parser("status", help="show queues and recent jobs")
    sub.add_parser("doctor", help="print hardware detection and storage checks")

    serve = sub.add_parser("serve", help="run the service loop")
    serve.add_argument("--once", action="store_true", help="single pass then exit")

    return parser


def _load_config(args: argparse.Namespace) -> Config:
    try:
        return config_mod.load(args.config)
    except FileNotFoundError:
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        raise SystemExit(2)
    except ValueError as exc:
        print(f"error: invalid config: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _reachable(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        path.mkdir(parents=True, exist_ok=True)
        return os.access(path, os.W_OK)
    except OSError:
        return False


def _cmd_doctor(cfg: Config) -> int:
    from kiln.hwprobe import probe

    hw = probe()
    print("kiln doctor")
    print("-----------")
    print(f"GPU:                {hw.gpu_name or '(none detected)'}")
    print(f"VRAM (MiB):         {hw.vram_mb if hw.vram_mb is not None else '(unknown)'}")
    print(f"Compute capability: {hw.compute_capability or '(unknown)'}")
    print(f"NVENC H.264/HEVC:   {hw.nvenc_h264}/{hw.nvenc_hevc}")
    print(f"NVENC AV1:          {hw.nvenc_av1}  (Ada/RTX-40+ only)")
    print(f"CUDA available:     {hw.cuda_available}")
    print(f"Whisper tier:       {hw.whisper_tier()}")
    print()

    ok = True
    checks = [("inbox", cfg.inbox), ("scratch_dir", cfg.scratch_dir), ("state_dir", cfg.state_dir)]
    devs: dict[str, int | None] = {}
    for name, path in checks:
        writable = _reachable(path)
        ok = ok and writable
        try:
            devs[name] = path.stat().st_dev
        except OSError:
            devs[name] = None
        print(f"{name:12} {path}  ->  {'OK' if writable else 'NOT WRITABLE'}")

    # Same-filesystem requirement (queue uses atomic rename across these three).
    same_fs = len({d for d in devs.values() if d is not None}) <= 1
    if not same_fs:
        ok = False
        print("ERROR: inbox, scratch_dir, and state_dir must be on the SAME filesystem "
              "(atomic rename). They are currently on different devices.")

    for name, archive in (("masters_archive", cfg.masters_archive),
                          ("exports_archive", cfg.exports_archive)):
        if archive is None:
            print(f"{name:16} (unset — jobs will hold in the pending-archive queue)")
        elif _reachable(archive):
            print(f"{name:16} {archive}  ->  reachable")
        else:
            print(f"{name:16} {archive}  ->  UNREACHABLE (jobs will defer)")

    print()
    print("doctor: OK" if ok else "doctor: PROBLEMS FOUND")
    return 0 if ok else 1


def _cmd_status(cfg: Config) -> int:
    layout = StateLayout(cfg.state_dir)
    layout.ensure()
    buckets = [("queued", layout.queued), ("processing", layout.processing),
               (PENDING, layout.pending), ("done", layout.done), ("failed", layout.failed)]
    for name, path in buckets:
        entries = sorted((p.name for p in path.iterdir() if p.is_dir()), reverse=True)
        recent = ", ".join(entries[:5]) if entries else "-"
        print(f"{name:16} {len(entries):4}  {recent}")
    return 0


def _cmd_run(cfg: Config, folder: str) -> int:
    src = Path(folder)
    if not src.is_dir():
        print(f"error: not a folder: {folder}", file=sys.stderr)
        return 2
    queue = ProcessingQueue(cfg.state_dir)
    job = queue.enqueue(src)
    print(f"enqueued: {job.job_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point (see ``[project.scripts]`` in pyproject.toml)."""
    args = _build_parser().parse_args(argv)
    cfg = _load_config(args)

    if args.command == "doctor":
        return _cmd_doctor(cfg)
    if args.command == "status":
        return _cmd_status(cfg)
    if args.command == "run":
        return _cmd_run(cfg, args.folder)
    if args.command == "serve":
        import logging

        from kiln import service

        # Send the "kiln" logger to stderr; systemd captures it into the journal. Message
        # only (no timestamp/level prefix) — journald already stamps time and unit. Set up
        # here (not at import) so library use and other subcommands stay silent.
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        svc_log = logging.getLogger("kiln")
        svc_log.setLevel(logging.INFO)
        svc_log.addHandler(handler)

        service.run(cfg, once=args.once)
        return 0
    raise AssertionError(f"unhandled command: {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
