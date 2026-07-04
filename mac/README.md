# kiln — Mac side

The Mac-side handoff for kiln. You edit in Final Cut Pro, export a ProRes master, and this
drops it onto **deb005** (the GPU box) for processing. Nothing is processed on the Mac.

- **`kiln-submit.js`** — the handoff helper, written in **JXA** (JavaScript for Automation),
  run by `osascript`. Chosen because `osascript` is always present on macOS (the system
  `python3` is an Xcode Command Line Tools stub that prompts to install). It builds a
  `job.json`, and atomically drops it plus the master into deb005's mounted SMB inbox.

Full design and the FCP/Compressor wiring: **`../docs/specs/2026-07-03-mac-controller-spec.md`**.

## Install on the Mac

1. Pull the repo to the Mac (over SSH from deb005 — see the pipeline docs) or copy this file.
2. Put the helper where the Automator step expects it:
   ```bash
   mkdir -p ~/bin
   cp mac/kiln-submit.js ~/bin/kiln-submit.js
   ```
3. Create the config so you don't repeat `--inbox` every time: `~/.config/kiln/submit.json`
   ```json
   {
     "inbox": "/Volumes/kiln-inbox",
     "submit_url": "http://deb005:8765/submit",
     "default_preset": "standard"
   }
   ```
   (`submit_url` only works once deb005's submit endpoint is exposed on the LAN — spec §8.
   Leave it `""` otherwise; jobs are still picked up within ~2s.)
4. Mount the inbox share: Finder → ⌘K → `smb://deb005/kiln-inbox` as the `kilndrop` user.

## Verify the helper works (before wiring FCP)

These two commands isolate any problem in seconds, before Compressor is involved.

**Dry run — writes nothing, just prints the job.json:**
```bash
osascript ~/bin/kiln-submit.js /path/to/any.mov --preset standard --dry-run
```
Expect a `# DRY RUN` banner, the derived `job_id`, and a valid `job.json`.

**Real drop to a scratch folder — exercises the file ops (staging + atomic rename):**
```bash
mkdir -p /tmp/kiln-test
osascript ~/bin/kiln-submit.js /path/to/any.mov --preset standard --inbox /tmp/kiln-test
ls -la /tmp/kiln-test/*/          # expect <job_id>/master.mov + job.json
```

If either fails with an ObjC-bridge error (a method name not resolving), that's the thing to
fix — the JS logic is verified, but the bridge calls only run under `osascript` on macOS.

## Daily use (after the FCP/Compressor setup in the spec)

**Final Cut Pro → File → Share → "kiln — Standard"** (or 4K / Upscale / Transcode-only). That's
the whole interaction — the Compressor job action runs `kiln-submit.js` for you. You never run
it by hand.

## Presets

| Preset | Steps run |
|---|---|
| `standard` | transcode, captions, normalize, chapters, metadata |
| `4k` | same as standard (deb005 auto-picks HEVC for 4K) |
| `upscale` | standard + Real-ESRGAN upscale |
| `transcode-only` | transcode + normalize only |
