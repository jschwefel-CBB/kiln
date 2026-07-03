# kiln — follow-ups

Known gaps and deferred work, captured so they aren't lost. Not blocking; each is a
self-contained future change. Promote to GitHub Issues when picked up.

## Per-job `options` are not threaded to steps

**Found:** 2026-07-02, during Phase 4 bring-up.

`job.json` advertises a per-job `options` block in the spec
(`options.whisper_model`, `options.llm_model`, `options.codec`, `options.target_lufs`),
but the runner only reads the `jobs` toggles from `job.json` — it never parses `options`,
and `StepContext` carries no options field. As a result:

- `transcribe.py` resolves its model from `config.whisper_model` only; a per-job
  `options.whisper_model` override is silently ignored.
- `metadata.py` likewise uses `config.llm_model` only.
- `transcode.py` / `normalize.py` use `config.codec` / `config.target_lufs` only.

**Fix (own change + tests):**
1. Add `options: dict[str, object]` to `StepContext`.
2. In `runner.py`, parse `spec.get("options", {})` from `job.json` and pass it into each
   `StepContext`.
3. Update the affected steps to prefer the per-job option over the config default
   (e.g. `ctx.options.get("whisper_model") or ctx.config.whisper_model`).
4. Tests: a job whose `options.whisper_model` differs from config resolves to the job's
   value; absent options fall back to config.

Until then, the config file is the single source for model/codec/LUFS selection, and the
`options` block in `job.json` is inert.

## Service does not log job lifecycle to the journal

**Found:** 2026-07-02.

`journalctl -u kiln` shows only systemd's start/stop lines — the running service emits no
application-level log (watcher armed, submit endpoint bound, job picked up / step
started / archived). Proof-of-life currently comes from the socket bind and the on-disk
`result.json` / `job.log`. A few `logging` calls at INFO on the serve path (startup
banner, per-job lifecycle) would make the journal self-documenting and ops-friendly.
Non-blocking; `result.json` + `job.log` already capture per-job outcomes.
