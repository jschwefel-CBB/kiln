# kiln — Mac Controller Spec (Phase 5)

**Status:** specification for implementation on the Mac. This document is written so an
engineer (or Claude Code running on the Mac) with **no prior kiln context** can build the
Mac side end-to-end. The Linux side (`deb005`) is already built and running — this spec
only covers the Mac.

**Audience assumption:** you know macOS and Final Cut Pro as a user, but nothing about
kiln, its job contract, or how `deb005` consumes work. Everything you need is here.

---

## 1. What you are building, and why

You edit YouTube videos in **Final Cut Pro** on a Mac. When a cut is done you export a
**ProRes master**. Today that master would be encoded and captioned on the Mac — slow, CPU-
bound work. kiln moves all of that to an idle **RTX A4000 Linux box named `deb005`**, which
does GPU transcoding plus AI captioning/transcript/chapters/metadata, then archives
everything to a storage server. The Mac's only job is to **hand the master to `deb005`**.

You are building the **Mac-side handoff**: a small Python helper (`kiln-submit`) plus the
Final Cut Pro integration that invokes it automatically when you finish an export. That's
it. No processing happens on the Mac.

**The whole Mac side reduces to three actions:**
1. Put the exported master file into a folder that `deb005` watches (an SMB share).
2. Write a tiny `job.json` next to it describing what work to do.
3. (Optional) Ping `deb005` so it starts immediately instead of within ~2 seconds.

---

## 2. The contract with deb005 (do not change this — deb005 depends on it)

`deb005` exposes a folder called the **inbox** as an SMB share. A **job** is a folder inside
the inbox containing:

- **the master video** — any of `.mov`, `.mp4`, `.mxf`, `.mkv` (ProRes `.mov` in practice),
  named anything **except** `upload.mp4` (that name is reserved for kiln's output).
- **`job.json`** — a sidecar describing the job (schema below).

`deb005` watches the inbox. It considers a job folder **ready** only when **both** are true:
1. `job.json` exists **and** a master file exists, and
2. **nothing in the folder has been modified in the last 2 seconds** (a stability gate so a
   half-copied master is never picked up mid-write).

Once ready, `deb005` moves the folder out of the inbox and processes it. **Therefore the Mac
must finish writing the master before the 2-second timer starts** — see the atomic-drop rule
in §5. This is the single most important correctness requirement on the Mac side.

### 2.1 `job.json` schema

```json
{
  "job_id": "2026-07-03_my-video-title",
  "source": "master.mov",
  "title": "My Video Title",
  "jobs": {
    "transcode": true,
    "captions":  true,
    "normalize": true,
    "chapters":  true,
    "metadata":  true,
    "upscale":   false
  },
  "options": {
    "codec": "auto",
    "target_lufs": -14,
    "whisper_model": "auto",
    "llm_model": "llama3.1:8b",
    "keep_master": false
  }
}
```

**Field semantics:**

| Field | Meaning |
|---|---|
| `job_id` | Unique id; becomes the archive folder name on the storage server. Use `YYYY-MM-DD_slug`. Must be filesystem-safe: lowercase, `[a-z0-9._-]`, no spaces. |
| `source` | Filename of the master within the folder. Informational; deb005 auto-detects the master by suffix. |
| `title` | Human title, used by the metadata step as a hint. Optional. |
| `jobs.*` | Which pipeline steps to run. Any omitted key defaults **off** on deb005. `captions` drives the transcript that `chapters` and `metadata` depend on — if you want chapters/metadata, keep `captions` on. |
| `options.codec` | `auto` (deb005 picks HEVC for 4K, H.264 for ≤1080p), or force `hevc`/`h264`. |
| `options.target_lufs` | Loudness target; YouTube reference is `-14`. |
| `options.whisper_model` | `auto` (deb005 sizes by its VRAM) or a specific model (`small`, `medium`, `large-v3`). |
| `options.llm_model` | Ollama model for metadata drafts. |
| `options.keep_master` | `true` pins the master against retention pruning (evergreen content). |

**Options precedence:** each `options` key overrides deb005's `config.toml` default **for
that job only**; omit a key (or set it to `null`) to accept deb005's default. You rarely need
to set `options` at all — the presets in §4 mostly just vary the `jobs` toggles.

### 2.2 Outputs (for your awareness — the Mac never handles these)

deb005 produces `upload.mp4`, `captions.srt`, `transcript.txt`, `chapters.txt`,
`metadata.md`, `result.json`, `job.log`, and archives them (with the master) to the storage
server. The Mac never sees these; they do not come back.

---

## 3. `kiln-submit` — the Python helper

**Language:** Python 3 (present on macOS; use only the standard library — `argparse`, `json`,
`pathlib`, `shutil`, `urllib.request`, `datetime`, `re`). No third-party dependencies, no
pip install.

**Install location:** `~/bin/kiln-submit` (chmod +x, shebang `#!/usr/bin/env python3`).

**What it does:**
1. Take a master file path + a preset (or explicit flags).
2. Derive a `job_id` (from `--job-id`, else `YYYY-MM-DD_<sanitized-basename>`).
3. Build `job.json` from the preset/flags.
4. **Atomically drop** the master + `job.json` into the mounted inbox share (§5).
5. (Optional) POST to deb005's submit endpoint for instant pickup (§6).
6. Print the `job_id` and the destination path; exit non-zero on any error.

### 3.1 CLI

```
kiln-submit MASTER [--preset NAME] [--job-id ID] [--title TITLE]
                   [--inbox PATH] [--submit-url URL]
                   [--transcode/--no-transcode] [--captions/--no-captions]
                   [--normalize/--no-normalize] [--chapters/--no-chapters]
                   [--metadata/--no-metadata] [--upscale/--no-upscale]
                   [--codec auto|hevc|h264] [--target-lufs N]
                   [--whisper-model auto|small|medium|large-v3] [--llm-model NAME]
                   [--keep-master] [--dry-run]
```

- `MASTER` — path to the exported master. Required.
- `--preset` — one of the names in §4. A preset sets the `jobs` toggles; explicit
  `--*/--no-*` flags override individual toggles after the preset is applied.
- `--inbox` — the mounted inbox path (default from config, §3.3).
- `--submit-url` — deb005 submit endpoint (default from config; empty string disables ping).
- `--dry-run` — build and print the `job.json` and intended destination, write nothing.

**Precedence:** built-in preset defaults → `--preset` → individual flags → config-file
defaults for `--inbox`/`--submit-url`. Fail with a clear message (exit 2) if `MASTER`
doesn't exist, if the derived `job_id` is unsafe and can't be sanitized, or if the inbox
path is not currently mounted/writable.

### 3.2 job_id derivation and sanitization

- If `--job-id` given, sanitize it; else `job_id = f"{YYYY-MM-DD}_{slug(MASTER stem)}"`.
- `slug(x)`: lowercase, spaces→`-`, strip anything not `[a-z0-9._-]`, collapse repeats,
  trim leading/trailing `-._`, cap length ~60. If the result is empty, exit 2 with a message.
- **No spaces ever** in `job_id` or any file/folder name kiln creates.

### 3.3 Config file (optional, keeps flags short)

`~/.config/kiln/submit.toml` (parse with `tomllib`, stdlib in Python 3.11+):

```toml
inbox = "/Volumes/kiln-inbox"          # where the deb005 inbox SMB share is mounted
submit_url = "http://deb005:8765/submit" # empty "" to disable the instant-trigger ping
default_preset = "standard"
```

If the file is absent, `--inbox` is required and `--submit-url` defaults to empty (ping off).

---

## 4. Presets (the FCP Share Destinations map to these)

Each preset only varies the `jobs` toggles; `options` stay at `auto`/defaults unless noted.

| Preset | transcode | captions | normalize | chapters | metadata | upscale | Use |
|---|:--:|:--:|:--:|:--:|:--:|:--:|---|
| **standard** | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | The everyday full package. |
| **4k** | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | Same steps; `options.codec` left `auto` (deb005 picks HEVC for 4K). Present as a distinct destination so 4K exports are unambiguous. |
| **upscale** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Adds opt-in Real-ESRGAN upscale (slow; for sub-4K sources worth enlarging). |
| **transcode-only** | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ | Just a compact upload file + loudness; no AI artifacts. |

---

## 5. Atomic drop (correctness-critical)

deb005's 2-second stability gate protects against a half-written master, **but only if the
Mac never leaves a partially-copied file sitting in the inbox with a `job.json` already
beside it.** The helper MUST use a staging-then-rename pattern so the job appears in the
inbox complete:

1. Create a **staging folder on the same filesystem as the inbox** — e.g.
   `<inbox>/.staging/<job_id>/`. **deb005's watcher explicitly skips dot-prefixed folders**,
   so a job being assembled under `.staging/` is never picked up mid-copy, even if it
   momentarily looks complete and stable. (This is a guaranteed part of the contract, not a
   convention.)
2. Copy the master into the staging folder (`shutil.copy2`), then write `job.json` there.
3. **Atomically move the completed staging folder into place**: `os.rename(staging, <inbox>/<job_id>)`.
   `os.rename` within one filesystem is atomic, so the job folder appears fully formed; the
   2-second timer then starts on already-complete contents.

> **Why not copy directly into `<inbox>/<job_id>/`?** Because SMB copies of a 60–120 GB
> ProRes master take time; if `job.json` is written first (or the master is still streaming),
> deb005 could see "job.json + a growing master" and, once writes pause for 2s mid-transfer
> (e.g. a network hiccup), pick it up incomplete. Staging + atomic rename removes that race.

If the inbox share and the staging dir cannot be on the same filesystem (they always can —
same share), fall back to: copy master as `master.mov.partial`, then `job.json.partial`,
then rename both to their final names master-first, `job.json` **last** (deb005 needs
`job.json` present as the final trigger). Document this fallback but prefer the folder-rename.

---

## 6. The instant-trigger ping (requires a deb005 change — see §8)

deb005 runs a tiny HTTP endpoint that, on `POST /submit`, tells kiln to scan the inbox
**now** instead of waiting for its next 2-second poll. From the Mac:

```python
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request(submit_url, method="POST"), timeout=3)
except Exception:
    pass  # best-effort: if the ping fails, deb005 still picks the job up within ~2s
```

The ping carries **no payload and no path** — it only says "look now." The job is already in
the inbox from §5; the ping just shaves off up to ~2 seconds of latency. It must be
**best-effort**: never fail the submit because the ping didn't go through.

**This endpoint is bound to `127.0.0.1` by default on deb005 and is NOT reachable from the
Mac until deb005 is reconfigured to listen on the LAN — see §8.** Until then, set
`submit_url = ""` (ping disabled) and rely on the 2-second poll.

---

## 7. Final Cut Pro integration (Compressor primary)

**Chosen mechanism: a Compressor "Run Automator Workflow" job action.** You already have
Compressor as part of **Apple Creator Studio** (no extra cost). Compressor is preferred over
a Folder Action because its post-transcode action runs **only after the export file is fully
written**, eliminating the partial-file race at the source; it is Apple's supported
automation surface and passes the output file path to the workflow.

### 7.1 One-time setup

1. **Create an Automator "Quick Action"/workflow** (`kiln-handoff.workflow`) containing a
   single **"Run Shell Script"** step, input = "as arguments":
   ```bash
   # $1 is the path Compressor passes (the exported master)
   /Users/<you>/bin/kiln-submit "$1" --preset standard
   ```
   (Create one workflow per preset, or read the preset from the output filename — start with
   one per preset; it's the simplest and most predictable.)

2. **In Compressor, build a ProRes PASS-THROUGH / copy setting** — **critical:** Compressor
   must **not** re-encode. kiln exists precisely so deb005 does the GPU transcode; the Mac
   must hand off the ProRes master untouched. Use a ProRes setting that matches the source
   (or a "copy"/passthrough), so Compressor's output is effectively the master.

3. **Attach the job action:** in that Compressor setting (or job), add job action **"Run
   Automator Workflow"** → select `kiln-handoff.workflow`. Compressor runs it after the
   (pass-through) transcode, handing the file path to `kiln-submit`.

4. **Save the Compressor setting**, then in **Final Cut Pro** add a **Compressor Presets
   destination** pointing at it: **File → Share → Add Destination**, double-click the
   **Compressor Settings** icon, and choose your saved preset. (Compressor must be installed —
   it is, via Creator Studio.) Name the destinations to match presets: **"kiln — Standard"**,
   **"kiln — 4K"**, **"kiln — Upscale"**, **"kiln — Transcode-only"** (each tied to a
   Compressor setting whose Automator step passes the matching `--preset`). Ref:
   <https://support.apple.com/guide/final-cut-pro/compressor-presets-destination-ver74e31fd6c/mac>.

### 7.2 Daily use

In Final Cut Pro: **File → Share → "kiln — Standard"** (or the preset you want). FCP sends
the timeline to Compressor, Compressor passes the ProRes through and fires the Automator
workflow, which runs `kiln-submit`, which atomically drops the job into the inbox. deb005
takes it from there. **Picking the Share Destination IS choosing the job.**

### 7.3 Alternative (free, no Compressor): Folder Action

Documented for completeness; use only if you don't want Compressor in the loop:

- In FCP, add a Share Destination **"Export File"** that writes the master to a **staging
  folder** (NOT the inbox directly).
- Attach a macOS **Folder Action** (`Automator` → Folder Action bound to that staging folder)
  running `kiln-submit "$1" --preset standard`.
- **Caveat:** Folder Actions can fire while a large ProRes file is still being written. To be
  safe, `kiln-submit` already stages+atomic-renames (§5), but the Folder Action itself may
  trigger on the partial file — add a settle check in the shell step (wait until the file
  size is stable for 3 seconds before calling `kiln-submit`). Compressor avoids this entirely,
  which is why it's primary.

---

## 8. deb005-side change required for the ping (§6)

Exposing the submit endpoint on the LAN is a **config-only change on deb005** (no code
change). The endpoint's bind host is `config.toml`'s `[submit] host` (default `127.0.0.1`).

1. Edit `/opt/kiln/config.toml` on deb005:
   ```toml
   [submit]
   host = "0.0.0.0"   # was 127.0.0.1 — now reachable on the LAN
   port = 8765
   ```
   (Or bind to the specific LAN IP rather than all interfaces, e.g. `host = "172.31.1.100"`.)
2. `sudo systemctl restart kiln`; confirm with `sudo ss -ltnp | grep 8765` that it now shows
   `0.0.0.0:8765` (or the LAN IP) instead of `127.0.0.1:8765`.
3. On the Mac, set `submit_url = "http://deb005:8765/submit"` in `~/.config/kiln/submit.toml`.

**Security note (honest scope):** the endpoint accepts **no payload and no path** — a
`POST /submit` only causes kiln to scan its own inbox immediately. The worst a hostile LAN
host can do is trigger redundant inbox scans (cheap, harmless). It cannot inject a job, a
path, or a command through this endpoint. Even so, prefer binding to the **specific LAN IP**
over `0.0.0.0`, and rely on the LAN being trusted. If that ever feels too open, drop the ping
entirely (`submit_url = ""`) and accept the ~2-second poll latency — the pipeline is
identical either way.

---

## 9. Acceptance criteria

Build is complete when:

1. **`kiln-submit --dry-run`** on a sample `.mov` prints a valid `job.json` with the right
   preset toggles and a sanitized `YYYY-MM-DD_slug` `job_id`, writing nothing.
2. **Real submit** of a small `.mov` atomically creates `<inbox>/<job_id>/` containing the
   master + `job.json`, with no partial-file window (verify by watching the inbox during a
   large-file submit — the folder appears only when complete).
3. **deb005 picks it up** within ~2s of the drop (or immediately if the ping is enabled and
   §8 is done), and the job lands in deb005's `state/done/` with outputs archived — confirm
   via `journalctl -u kiln` showing `job <id>: processing` → `done (archived)`.
4. **Each FCP Share Destination** ("kiln — Standard/4K/Upscale/Transcode-only") produces a job
   with the corresponding `jobs` toggles, verified by reading the archived `result.json`.
5. **Compressor does not re-encode** — the archived master byte-matches (or is ProRes-
   equivalent to) the FCP export; deb005's transcode step is what produced `upload.mp4`.
6. **No spaces** in any filename/foldername kiln creates; no third-party Python deps; no
   secrets in the helper or config committed to any repo.

---

## 10. Out of scope (explicitly)

- Any processing on the Mac (transcode/caption/etc.) — that is deb005's job.
- Bringing outputs back to the Mac — they are archived on the storage server.
- Code signing / notarization / Developer ID — the Workflow Extension path was rejected in
  the design; `kiln-submit` is a plain script invoked by Automator/Compressor, which needs no
  signing.
- Mounting the SMB share — assumed already mounted at `--inbox` (Finder → Connect to Server →
  `smb://deb005/kiln-inbox` as the registered `kilndrop` user; see the deb005 deployment
  runbook §4). Optionally document auto-mount via a login item, but the helper only needs the
  share present and writable.
