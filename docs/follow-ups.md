# kiln — follow-ups

Known gaps and deferred work, captured so they aren't lost. Not blocking; each is a
self-contained future change. Promote to GitHub Issues when picked up.

## Resolved

### Per-job `options` are now threaded to steps — RESOLVED 2026-07-02

`job.json`'s `options` block (`whisper_model`, `llm_model`, `codec`, `target_lufs`) now
overrides the corresponding config value for that one job. Precedence is resolved once in
the runner (`kiln.runner.effective_config`): it overlays only those four keys onto the base
`Config` via `dataclasses.replace`, ignoring absent/null keys and never leaking unknown
keys. Steps are unchanged — each still reads `ctx.config.<field>` and is unaware options
exist. Absent an `options` block, the base config is used unchanged. Covered by
`tests/test_runner.py` (`test_effective_config_*`, `test_run_job_threads_options_*`).

### Service logs job lifecycle to the journal — RESOLVED 2026-07-02

The service now logs to the `kiln` logger, which the `serve` command routes to stderr →
captured by journald, so `journalctl -u kiln` shows: the startup banner (watched inbox +
submit endpoint host:port), crash-recovery of an orphaned job, and per-job lifecycle
(`processing`, `done (archived)`, `archive deferred`, or `FAILED — <reason>` at WARNING).
Message-only format (journald stamps time + unit). Covered by
`tests/test_service_logging.py`. `result.json` / `job.log` remain the per-job on-disk
record; the journal is the operational stream.

## Open

_(none currently — add new items here as they surface)_
