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

You are building the **Mac-side handoff**: a small JXA helper (`kiln-submit.js`) plus the
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

## 3. `kiln-submit` — the JXA helper

**Language:** **JXA (JavaScript for Automation), run by `osascript`.** Chosen over Python
because `osascript` is **always present on macOS** (Apple's built-in automation runtime),
whereas the system `python3` is an Xcode Command Line Tools stub that prompts to install on a
clean Mac. JXA is JavaScript, so JSON is native; file operations use the Objective-C bridge
(`$.NSFileManager`, `$.NSData`). It is plain text, so it lives in the kiln repo (`mac/kiln-submit.js`)
and is pulled to the Mac like the rest of the project, and it slots directly into the
Folder Action's "Run Shell Script" step (§7).

**Install location:** `mac/kiln-submit.js` in the repo, copied to (e.g.) `~/bin/kiln-submit.js`
on the Mac. Invoked as `osascript ~/bin/kiln-submit.js <args…>`.

**What it does:**
1. Take a master file path + a preset (and optional overrides) as `osascript` arguments.
2. Derive a `job_id` (from `--job-id`, else `YYYY-MM-DD_<sanitized-basename>`).
3. Build `job.json` from the preset/overrides.
4. **Atomically drop** the master + `job.json` into the mounted inbox share (§5).
5. (Optional) fire the deb005 submit endpoint for instant pickup (§6).
6. Print the `job_id` and the destination path to stdout; exit non-zero on any error.

### 3.1 Invocation

`osascript` passes everything after the script path as string arguments (available via
`run(argv)` in JXA). The argument grammar mirrors a CLI:

```
osascript kiln-submit.js MASTER [--preset NAME] [--job-id ID] [--title TITLE]
                                [--inbox PATH] [--submit-url URL]
                                [--transcode true|false] [--captions true|false]
                                [--normalize true|false] [--chapters true|false]
                                [--metadata true|false] [--upscale true|false]
                                [--codec auto|hevc|h264] [--target-lufs N]
                                [--whisper-model auto|small|medium|large-v3] [--llm-model NAME]
                                [--keep-master true|false] [--dry-run]
```

- `MASTER` — path to the exported master (the Folder Action passes this). Required, first positional.
- `--preset` — one of the names in §4. A preset sets the `jobs` toggles; explicit
  `--<step> true|false` flags override individual toggles after the preset is applied.
- `--inbox` — the mounted inbox path (default from config, §3.3).
- `--submit-url` — deb005 submit endpoint (default from config; empty string disables ping).
- `--dry-run` — build and print the `job.json` and intended destination, write nothing.

> Note: JXA has no argparse; the script parses `argv` itself with a tiny loop (positional
> first, then `--key value` pairs, with `--dry-run` and bare `--keep-master` treated as
> boolean flags). Keep the parser permissive but validate required inputs.

**Precedence:** built-in preset defaults → `--preset` → individual flags → config-file
defaults for `--inbox`/`--submit-url`. Fail with a clear message (exit 2) if `MASTER`
doesn't exist, if the derived `job_id` is unsafe and can't be sanitized, or if the inbox
path is not currently mounted/writable.

### 3.2 job_id derivation and sanitization

- If `--job-id` given, sanitize it; else `job_id = "<YYYY-MM-DD>_" + slug(basename-without-ext)`.
- `slug(x)`: lowercase, spaces→`-`, strip anything not `[a-z0-9._-]`, collapse repeats,
  trim leading/trailing `-._`, cap length ~60. If the result is empty, exit 2 with a message.
- **No spaces ever** in `job_id` or any file/folder name kiln creates.

### 3.3 Config file (optional, keeps invocations short)

`~/.config/kiln/submit.json` — **JSON**, read natively by JXA (no TOML parser needed):

```json
{
  "inbox": "/Volumes/kiln-inbox",
  "submit_url": "http://deb005:8765/submit",
  "default_preset": "standard"
}
```

- `inbox` — where the deb005 inbox SMB share is mounted.
- `submit_url` — instant-trigger endpoint; `""` (or omit) to disable the ping.
- `default_preset` — used when no `--preset` is given.

If the file is absent, `--inbox` is required and the ping defaults to off.

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

The simplest, dependency-free way from JXA is to shell out to `curl` (always present) via
`Application('System Events')` / `doShellScript`, best-effort:

```javascript
// best-effort ping; never throw. curl returns quickly with -m (max time).
try {
  const app = Application.currentApplication();
  app.includeStandardAdditions = true;
  app.doShellScript(`curl -s -m 3 -X POST ${JSON.stringify(submitUrl)} >/dev/null 2>&1 || true`);
} catch (e) { /* ignore: deb005 still picks the job up within ~2s via its poll */ }
```

The ping carries **no payload and no path** — it only says "look now." The job is already in
the inbox from §5; the ping just shaves off up to ~2 seconds of latency. It must be
**best-effort**: never fail the submit because the ping didn't go through.

**This endpoint is bound to `127.0.0.1` by default on deb005 and is NOT reachable from the
Mac until deb005 is reconfigured to listen on the LAN — see §8.** Until then, set
`submit_url = ""` (ping disabled) and rely on the 2-second poll.

---

## 7. Final Cut Pro integration (Export File → Folder Action primary)

**Chosen mechanism: an FCP "Export File" Share Destination → staging folder → macOS Folder
Action → `kiln-submit`.** FCP writes its exact ProRes master with **no re-encode**, a Folder
Action bound to the staging folder fires when the file lands, and its shell step hands the
master's path to `kiln-submit`, which atomically drops the job into the inbox.

**Why not Compressor (rejected — see §7.4).** The original design named a Compressor "Run
Automator Workflow" job action as primary, on the assumption that Compressor could be set to
**pass the ProRes through without re-encoding**. Verified on the physical Mac (M4 mini): the
Compressor shipped in **Apple Creator Studio** (com.apple.CompressorApp v5.3, sandboxed) has
**no copy/passthrough setting** — every ProRes setting **re-encodes** to ProRes. That directly
violates the non-negotiable "Compressor must not re-encode; hand off the master untouched"
requirement (§7.1 step 2 of the old design; acceptance §5). A ProRes→ProRes re-encode wastes
Mac time, adds an encode generation, and produces a master that is *not* the FCP export. So
Compressor is dropped from the loop and the Export-File/Folder-Action path — which literally
produces FCP's untouched master — is primary.

### 7.1 One-time setup

1. **Create the staging folders** — one per preset, so each Folder Action can hardcode its
   `--preset` and there is no filename-parsing fragility. No spaces in any path:
   ```bash
   mkdir -p "$HOME/kiln-staging/standard" "$HOME/kiln-staging/4k" \
            "$HOME/kiln-staging/upscale" "$HOME/kiln-staging/transcode-only"
   ```
   These are **staging** folders, NOT the inbox. FCP exports here; the Folder Action then calls
   `kiln-submit`, which stages+atomic-renames into the mounted inbox (`/Volumes/kiln-inbox`).

2. **Attach a Folder Action to each staging folder.** For each preset folder, bind an
   `Automator` **Folder Action** whose single **"Run Shell Script"** step (shell `/bin/bash`,
   pass input **"as arguments"**) runs the settle-check-then-submit below. Change only the
   `--preset` value per folder (`standard`, `4k`, `upscale`, `transcode-only`):
   ```bash
   # Folder Actions can fire while a large ProRes master is still being written. Wait until the
   # file size is stable for 3 consecutive seconds before handing off, so kiln-submit never
   # stages a partial file. (kiln-submit's own stage+atomic-rename protects deb005 from ever
   # SEEING a half-copied job; this guard protects the Mac side from starting on a partial one.)
   for f in "$@"; do
     case "$f" in */.*) continue;; esac          # ignore dotfiles/temp
     last=-1
     while :; do
       cur=$(/usr/bin/stat -f%z "$f" 2>/dev/null) || break
       [ "$cur" = "$last" ] && [ "$cur" -gt 0 ] && break
       last=$cur
       /bin/sleep 3
     done
     /usr/bin/osascript "$HOME/bin/kiln-submit.js" "$f" --preset standard
   done
   ```
   (The `--preset standard` on the last line is what changes per folder.) The size-settle loop
   is mandatory — it is the Folder-Action equivalent of Compressor's "run only after the export
   is fully written" guarantee.

3. **Add one FCP "Export File" Share Destination per preset.** In **Final Cut Pro → File →
   Share → Add Destination**, double-click **Export File**, and set its output folder to the
   matching staging folder from step 1. Name the destinations to match presets: **"kiln —
   Standard"**, **"kiln — 4K"**, **"kiln — Upscale"**, **"kiln — Transcode-only"**. Set each to
   export a **ProRes** master (matching the source — this is FCP's own export, no re-encode
   beyond FCP's normal master render). Ref:
   <https://support.apple.com/guide/final-cut-pro/add-destinations-ver2b3f4c9d7/mac>.

### 7.2 Daily use

In Final Cut Pro: **File → Share → "kiln — Standard"** (or the preset you want). FCP exports
the ProRes master into that preset's staging folder; the Folder Action waits for the file to
settle, then runs `kiln-submit`, which atomically drops the job into the inbox. deb005 takes
it from there. **Picking the Share Destination IS choosing the job.**

### 7.3 The atomic-drop chain (why no partial file is ever processed)

Two independent guards, either of which is sufficient, applied in series:

1. **Mac Folder Action** waits until the export file's size is stable for 3 s before calling
   `kiln-submit` (§7.1 step 2) — so the helper never starts on a partial file.
2. **`kiln-submit`** stages the master under `<inbox>/.staging/<job_id>/` and **atomic-renames**
   the finished job folder into place (§5) — so deb005 never *sees* a half-assembled job.
3. **deb005** additionally treats a job as ready only when nothing in the folder has changed for
   2 s (§2) — a third backstop.

### 7.4 Compressor (rejected — recorded so this is not re-litigated)

Compressor was the original primary mechanism and is **rejected**. On the physical M4 mini,
Creator Studio's Compressor 5.3 offers **no ProRes copy/passthrough** — all ProRes settings
re-encode, violating acceptance §5. Custom settings and the "Run Automator Workflow" job action
are also GUI-only (sandboxed group container; not creatable via CLI/file-drop), so it could not
even be wired programmatically. Should a future Compressor gain a true passthrough setting, it
could be reconsidered as an alternative, but the Export-File/Folder-Action path is simpler, free,
and provably re-encode-free, so there is no reason to.

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

1. **`osascript kiln-submit.js SAMPLE.mov --dry-run`** prints a valid `job.json` with the right
   preset toggles and a sanitized `YYYY-MM-DD_slug` `job_id`, writing nothing.
2. **Real submit** of a small `.mov` atomically creates `<inbox>/<job_id>/` containing the
   master + `job.json`, with no partial-file window (verify by watching the inbox during a
   large-file submit — the folder appears only when complete).
3. **deb005 picks it up** within ~2s of the drop (or immediately if the ping is enabled and
   §8 is done), and the job lands in deb005's `state/done/` with outputs archived — confirm
   via `journalctl -u kiln` showing `job <id>: processing` → `done (archived)`.
4. **Each FCP Share Destination** ("kiln — Standard/4K/Upscale/Transcode-only") produces a job
   with the corresponding `jobs` toggles, verified by reading the archived `result.json`.
5. **No Mac-side re-encode** — the archived master is FCP's own ProRes export (no Compressor in
   the loop, §7.4); deb005's transcode step is what produced `upload.mp4`.
6. **No spaces** in any filename/foldername kiln creates; no runtime install needed (JXA via
   the always-present `osascript`, no Python/Homebrew dependency); no
   secrets in the helper or config committed to any repo.

---

## 10. Out of scope (explicitly)

- Any processing on the Mac (transcode/caption/etc.) — that is deb005's job.
- Bringing outputs back to the Mac — they are archived on the storage server.
- Code signing / notarization / Developer ID — the Workflow Extension path was rejected in
  the design; `kiln-submit` is a plain script invoked by an Automator Folder Action, which needs
  no signing.
- Mounting the SMB share — assumed already mounted at `--inbox` (Finder → Connect to Server →
  `smb://deb005/kiln-inbox` as the registered `kilndrop` user; see the deb005 deployment
  runbook §4). Optionally document auto-mount via a login item, but the helper only needs the
  share present and writable.
