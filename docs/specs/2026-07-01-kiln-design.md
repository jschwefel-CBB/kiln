# Kiln — GPU Post-Processing Pipeline (Design Spec)

> Canonical design spec for `kiln`. Authored 2026-07-01. This is the source-of-truth
> design document; implementation follows the detailed plan derived from it.

## Context

**Why this is being built:** Edit YouTube content in Final Cut Pro on a Mac, then hand the
finished export to an RTX A4000 Linux box (`deb005`) for GPU-accelerated post-processing —
transcoding to a YouTube-ready file plus AI-generated artifacts (captions, transcript,
chapters, metadata drafts). This puts an otherwise-idle GPU to work and removes slow,
CPU-bound encoding/captioning from the Mac.

**Intended outcome:** A one-click (from inside FCP) or one-command (CLI) handoff that turns a
ProRes master into a complete YouTube upload package: compact upload video, `.srt` captions,
plain-text transcript, chapter timestamps, and draft description/titles/tags. The master is
dropped onto `deb005`, processed locally on the GPU, and then **both the master and all
outputs are moved to a dedicated storage server** as the final step — so `deb005` holds
nothing long-term and the storage server is the single archive.

## Public release & license

Likely released **source-available** (NOT OSI "open source"):

- **No hardcoded assumptions about hardware or paths** — the A4000 is *one detected
  configuration*, not the design center. No user-specific paths anywhere.
- **Runs on any reasonable NVIDIA GPU** (NVIDIA-only) — from ~8 GB to 24 GB cards. Detect
  NVENC generation and VRAM at runtime; software `libx264`/`libx265` is a safety-net fallback.
  AI steps auto-select model size by VRAM and fall back to CPU.
- **License: Business Source License 1.1 (BSL 1.1)** — **free for individuals and
  self-hosters; a company offering `kiln` as a commercial hosted/managed service must obtain a
  paid commercial license.** The BSL Additional Use Grant encodes the free-use scope; each
  release auto-converts to Apache-2.0 four years after publication (the change date). Implies a
  dual-license posture (BSL in-repo + a separately-granted commercial license). Call it
  **"source-available,"** never "open source."

## Architecture

Two machines joined by **one stable contract** (a job folder), so either side evolves
independently.

**Data flow (drop-local → process-local → archive-to-storage):** the drop lands on deb005's
local NVMe, processing is entirely local (no network I/O mid-job), and the final step moves
master + outputs to the storage server — **only if reachable** (else a local pending-archive
queue). Storage is a **write-once archive at the end**, never the source.

```
Mac Mini (FCP)                              deb005 (RTX A4000)                    Storage server
File>Share>"Post to deb005 — <preset>"      $INBOX (local NVMe, SMB-shared)       $MASTERS_ARCHIVE/<jobid>/
  or `kiln-submit` CLI                      kiln.service (systemd)                  { master.mov }
  - locate/render ProRes master             - watch $INBOX + localhost submit
  - write job.json sidecar          ──SMB─► - enqueue (strict-sequential, 1 GPU)   $EXPORTS_ARCHIVE/<jobid>/
  - export into deb005's $INBOX             - process on local NVMe scratch          { upload.mp4, captions.srt,
    (mounted SMB share)                     - write outputs locally                    transcript.txt, chapters.txt,
  - (optional) ping submit endpoint         - FINAL: move master → masters,            metadata.md, result.json }
                                              exports → exports (each if reachable),        ▲
                                              else hold in local pending-archive queue ─────┘
```

The master and the export package go to **two separate destinations** — the
large, irreplaceable master to `$MASTERS_ARCHIVE` (a protected pool, e.g. RAIDZ2)
and the small, re-derivable export package to `$EXPORTS_ARCHIVE` (a cheaper pool,
e.g. RAIDZ1). Either piece that cannot be moved (destination unset or offline)
waits in the local pending-archive queue and is drained later, independently.

**The contract (the seam):** a job = a folder (the master + a `job.json`) that appears in
deb005's local `$INBOX`. Any controller (FCP Share Destination, CLI, future web UI) just puts
that folder there; deb005 just consumes it.

**Transport & storage:**
- **Mac → deb005:** deb005 exposes `$INBOX` as an SMB share; the Mac mounts it and FCP exports
  the master (+ sidecar) straight into it. The folder-watcher sees the file locally.
- **Processing:** entirely on deb005 local NVMe. No network I/O during a job.
- **deb005 → storage:** final step moves the master to `$MASTERS_ARCHIVE` and the export
  package to `$EXPORTS_ARCHIVE`, each **only if reachable**; whatever cannot be moved waits
  in a local pending-archive queue and a periodic drainer retries.
- **Paths are configurable** (`$INBOX`, `scratch_dir`, `$MASTERS_ARCHIVE`, `$EXPORTS_ARCHIVE`).
  Works before the storage server exists — jobs accumulate in the pending-archive queue until
  the archive paths are set.
- **Network:** Mac, deb005, and storage are all on 10 GbE — a 60–120 GB 4K ProRes master
  copies in ~1–2 min; not a bottleneck.

**Engine model:** native systemd + Python orchestrator, strict-sequential single-GPU queue
(one video fully done before the next; GPU steps never contend). No Docker/Celery/Redis — YAGNI
for one GPU serving one user. The step interface is designed so steps *could* later move into
containers or a farm without redesigning the contract.

## Components

### deb005 engine (`~/repositories/kiln`, deployed to `/opt/kiln`)

- **`kiln/watcher.py`** — watch local `$INBOX/` + a localhost-only HTTP submit endpoint. Both enqueue.
- **`kiln/queue.py`** — persistent strict-sequential processing queue; state on disk; survives restart.
- **`kiln/runner.py`** — reads `job.json`, runs selected steps in dependency order (transcribe →
  chapters/metadata depend on transcript), writes outputs locally, writes `result.json`, hands
  the job to the archiver.
- **`kiln/archiver.py`** — final-step mover: on success move the master to
  `$MASTERS_ARCHIVE/<job_id>/` and the export package to `$EXPORTS_ARCHIVE/<job_id>/`, each if
  that destination is reachable/writable; else enqueue the unmoved piece into a persistent local
  pending-archive queue. A periodic drainer retries and frees local scratch once archived.
- **`kiln/steps/`** — uniform `run(ctx) -> StepResult`:
  - `transcode.py` — ffmpeg NVENC (NVIDIA-only), generation-aware codec selection from the
    capability probe. `hevc_nvenc`/`h264_nvenc` baseline; `av1_nvenc` only on Ada/RTX-40+;
    `libx265`/`libx264` safety-net. Auto-codec by resolution: HEVC for 4K, H.264 High for ≤1080p.
    High bitrate. MP4, AAC-LC 48 kHz.
  - `normalize.py` — ffmpeg `loudnorm` to -14 LUFS.
  - `transcribe.py` — faster-whisper, device auto (CUDA → CPU). Model auto by VRAM: `large-v3`
    on ≥~10 GB, smaller otherwise. → `captions.srt` + `transcript.txt`.
  - `chapters.py` — topic-segment transcript → `chapters.txt` (YouTube timestamps).
  - `metadata.py` — Ollama (default 8B); GPU or CPU-only. → `metadata.md` (description, titles, tags).
  - `upscale.py` — Real-ESRGAN, opt-in only; skipped with a clear message if hardware insufficient.
- **`kiln/hwprobe.py`** — runtime capability probe: NVIDIA GPU model + compute capability
  (→ NVENC generation → AV1 availability), VRAM (→ Whisper tier), CUDA availability, ffmpeg encoders.
- **`kiln/cli.py`** — `kiln status`, `kiln run <folder>`, `kiln doctor` (prints detected
  GPU/VRAM/NVENC-gen + AV1; verifies `$INBOX` writable and both `$MASTERS_ARCHIVE` /
  `$EXPORTS_ARCHIVE` reachable; checks models).
- **`kiln/config.py` + `config.toml`** — first-class storage config: `inbox` (local, SMB-shared),
  `scratch_dir` (local), `masters_archive` and `exports_archive` (two storage mounts).
  `config.example.toml` documents local/SMB/NFS. No paths committed. Works with either archive
  path unset. Env-var overrides.
- **`packaging/kiln.service`** — systemd unit; runs from `/opt/kiln` as a dedicated user.
- **`install.sh`** — service user, pinned venv, install to `/opt/kiln`, enable unit, run `kiln doctor`.

**`job.json` schema:**
```json
{
  "job_id": "2026-07-01_my-video",
  "source": "my-video.mov",
  "title": "My Video Title",
  "jobs": { "transcode": true, "captions": true, "normalize": true,
            "chapters": true, "metadata": true, "upscale": false },
  "options": { "codec": "auto", "target_lufs": -14,
               "whisper_model": "large-v3", "llm_model": "llama3.1:8b",
               "keep_master": false }
}
```

**Options precedence:** the four `options` knobs — `codec`, `target_lufs`,
`whisper_model`, `llm_model` — override the matching `config.toml` value **for that one
job**; any key omitted (or null) falls back to the config default. Precedence is resolved
once in the runner so each step just reads its effective config and is unaware options
exist. (`keep_master` governs retention, not a step — see Master retention.)

**Master retention (pruning):** ProRes masters are large (~220 GB/hr at 4K), so kiln
supports pruning the archived master after a rolling window while keeping the compressed
upload + artifacts permanently. Rules:
- A scheduled sweep (or a step run on a timer) deletes masters whose archive age exceeds
  `retention_days` (config; default 90).
- **Fail-safe:** because the master and its exports live on *separate* pools, a master is
  deleted only if `$EXPORTS_ARCHIVE` is reachable **and** the upload plus all derived
  artifacts are confirmed present in `$EXPORTS_ARCHIVE/<job_id>/`. Never prune when the
  exports pool is offline or any output is missing.
- **Off by default** (`prune_masters = false` in config) so a fresh/open-source install never
  deletes anyone's masters unexpectedly; the operator opts in explicitly.
- **Per-video override:** `"keep_master": true` in `job.json` pins that video's master
  permanently, exempting it from the sweep (evergreen/flagship/licensable content).

**Outputs** (produced locally, then archived to `$EXPORTS_ARCHIVE/<job_id>/`, separate from the
master in `$MASTERS_ARCHIVE/<job_id>/`): `upload.mp4`, `captions.srt`, `transcript.txt`,
`chapters.txt`, `metadata.md`, `job.log`, `result.json`.

**Error/observability:** steps fail-isolated (recorded in `result.json`; independent steps still
run); failed jobs move to a local `failed/` dir (never archived, never silently dropped); per-job
`job.log`. A job archives only after processing succeeds; if either archive destination is
unreachable the affected pieces wait in the pending-archive queue rather than failing.

### Mac-side controller (separate spec; built on the Mac)

One deliverable, two front doors — the helper *is* the CLI:

- **`kiln-submit`** — locate exported master, build `job.json` from flags/preset, copy master +
  sidecar into deb005's `$INBOX` (mounted SMB share), optional submit-endpoint ping.
- **FCP integration:** named Share Destinations ("Standard", "4K", "Upscale", "Transcode-only") —
  picking one *is* the job selection; each writes a fixed `job.json`. Hook: free Folder Action or
  one-time $49.99 Compressor post-action. No code signing, no recurring cost. The Workflow
  Extension path is rejected (FCP won't load unsigned in-process extensions; self-signed is
  fragile; Developer ID = $99/yr).

## FCP 12.3 native-feature overlap (why kiln still wins)

FCP 12.3 (2026-06-30) added on-device AI that partially overlaps kiln:

| kiln job | FCP 12.3 native? | Why kiln still does it |
|---|---|---|
| Transcode | No | Core reason for the box; offloads the Mac |
| Captions/transcript | Yes (US-English only, on-device) | Kept, default ON, toggleable. Whisper large-v3 (~2.7% WER clean) vs Apple (~15–18% WER); handles accents/non-English; transcript needed anyway for chapters+metadata, so the `.srt` is essentially free |
| Chapters | Partial (Edit Detection = shot splits) | Topic-based YouTube chapters from transcript |
| LLM metadata | No | Nothing native |
| -14 LUFS normalize | No one-click YouTube target | Automates the exact target |
| Upscale/denoise | No ESRGAN-class upscale | Opt-in AI upscale |

## Verification

- **Unit:** each step tested against a committed sample clip; GPU steps degrade gracefully without CUDA.
- **Integration:** drop a sample ProRes master into `$INBOX/` → outputs produced locally, then the
  master lands in `$MASTERS_ARCHIVE/<job_id>/` and the export package in `$EXPORTS_ARCHIVE/<job_id>/`.
- **Manual:** play `upload.mp4` (correct codec per source resolution); eyeball `.srt` sync; confirm
  -14 LUFS via ffmpeg loudnorm stats; sanity-check `chapters.txt` and `metadata.md`.
- **Service:** `systemctl status kiln`; `journalctl -u kiln`; `kiln status` shows both queues.
- **Resilience:** two videos at once → strict-sequential; restart mid-queue → resumes.
- **Archive-when-reachable:** either archive path unreachable → job processes, the affected pieces
  stay local in pending-archive; make reachable → drainer moves them + frees scratch.
- **Portability:** `kiln doctor` reports the A4000 (Ampere, no AV1) and selects large-v3 + HEVC/H.264.
- **Hygiene:** no user-specific path, secret, or `superpowers` path in the repo; `LICENSE` is BSL 1.1.
