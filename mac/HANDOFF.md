# kiln — Mac side handoff (for the agent doing the macOS work)

You are picking up the **Mac side** of the kiln pipeline. This file is your full briefing —
you should not need to reverse-engineer anything. Read this, then the spec, then verify the
helper, then do the FCP/Compressor wiring.

## The one-paragraph picture

The user edits YouTube videos in **Final Cut Pro** on a **base M4 Mac mini**. When a cut is
done, they export a **ProRes master**. kiln hands that master to a Linux GPU box, **`deb005`**
(RTX A4000), which transcodes it and generates captions/transcript/chapters/metadata, then
archives everything to a storage server. **Nothing is processed on the Mac.** Your job is the
**handoff**: make an FCP "Share" export automatically drop the master (plus a tiny `job.json`)
into a folder `deb005` watches. That's the whole Mac side.

Why offload at all, since the M4 is fast? Workflow, not speed: keep the Mac free while editing,
batch/fire-and-forget, and use the otherwise-idle A4000. (Decision is settled — don't re-open it.)

## Authoritative documents (read in this order)

1. **`docs/specs/2026-07-03-mac-controller-spec.md`** — the full spec. Source of truth. Job
   contract, `job.json` schema, presets, the atomic-drop rule, FCP/Compressor setup, and the
   deb005-side submit-port note. **If this handoff and the spec ever disagree, the spec wins.**
2. **`mac/README.md`** — install + the two-command verification of the helper.
3. This file — status, gotchas, and the exact task list.

## What is already DONE (do not redo)

- **The deb005 side is fully built, running, and verified.** The service (`kiln.service`) is
  live; it watches its inbox, processes jobs on the GPU, and archives to the storage server.
  End-to-end has been proven with a real ProRes-style clip. You do not touch deb005 except as
  noted below — it's ready and waiting for jobs.
- **`mac/kiln-submit.js`** — the handoff helper, **already written** (JXA / `osascript`). It
  parses args, builds `job.json`, and does the atomic staging→rename drop into the inbox. It is
  **syntax-valid** (checked with `node --check`) but **has NOT been run** — see the big caveat
  below. You do not need to write it; you need to **verify and wire** it.
- **The SMB inbox exists on deb005.** Share name **`kiln-inbox`**, authenticated as a dedicated
  user **`kilndrop`** (guest is off). The user has the `kilndrop` SMB password.
- **The submit endpoint is exposed on the LAN** (this was just done). `deb005` binds the
  instant-trigger ping on **`0.0.0.0:8765`** (all interfaces), reachable at
  **`http://172.31.1.100:8765/submit`** (or `http://deb005:8765/submit` if the name resolves).
  A `POST` there makes deb005 scan its inbox immediately instead of within ~2s. It takes no
  payload — worst case a hostile LAN host triggers a harmless redundant scan.

## THE BIG CAVEAT — the helper's file ops are untested

`kiln-submit.js` was written on the Linux box (deb005), which has **no JXA runtime**, so the
Objective-C bridge calls (`$.NSFileManager …`, `copyItemAtPathToPathError`, etc.) could not be
executed. JXA maps ObjC selectors to method names by a convention that is easy to get subtly
wrong, and such errors **only surface at runtime under `osascript`**. So:

**Before wiring anything into FCP/Compressor, run the two verification commands in
`mac/README.md`.** They isolate any bridge problem in seconds:

```bash
# 1. Dry run — writes nothing; must print a valid job.json:
osascript ~/bin/kiln-submit.js /path/to/any.mov --preset standard --dry-run

# 2. Real drop to a scratch dir — exercises the actual file ops (staging + atomic rename):
mkdir -p /tmp/kiln-test
osascript ~/bin/kiln-submit.js /path/to/any.mov --preset standard --inbox /tmp/kiln-test
ls -la /tmp/kiln-test/*/     # expect <job_id>/master.mov + job.json
```

If either fails with an ObjC-bridge error (a method name not resolving, an `undefined is not a
function`, etc.), **fix `kiln-submit.js`** — the pure-JS logic (arg parsing, presets, slug,
JSON) is verified, so any failure is in the bridge/file-ops layer. Common fixes: correct the
selector→method-name mapping, or fall back to `doShellScript` with `cp`/`mv`/`mkdir -p` for the
file ops (still atomic if `mv` is within one filesystem). Keep the staging→rename approach — it
is what makes deb005 never see a half-copied job (deb005's watcher skips dot-dirs, and the
helper stages under `<inbox>/.staging/<job_id>/`).

## Your task list (all Mac-side, all GUI except step 1)

1. **Install + verify the helper.** Copy `mac/kiln-submit.js` to `~/bin/kiln-submit.js`, create
   `~/.config/kiln/submit.json` (below), and run the two verification commands above. Do not
   proceed until the real drop to `/tmp/kiln-test` produces `<job_id>/master.mov` + `job.json`.

2. **Mount the inbox share.** Finder → ⌘K → `smb://deb005/kiln-inbox` (or
   `smb://172.31.1.100/kiln-inbox`) → **Registered User** `kilndrop` + the SMB password the user
   has. Confirm it mounts writable (the helper's real-drop test against the mounted path should
   succeed). Optionally add it as a login item to auto-mount.

3. **Build the Compressor setting — ProRes PASS-THROUGH.** In Compressor, create a setting that
   **does not re-encode** (ProRes passthrough/copy matching the source). This is critical: the
   Mac must hand off the master untouched — deb005 does the GPU transcode. Re-encoding on the
   Mac defeats the entire pipeline.

4. **Attach the job action.** To that Compressor setting add job action **"Run Automator
   Workflow"** pointing at an Automator workflow whose single **"Run Shell Script"** step
   (input = "as arguments") runs:
   ```bash
   /usr/bin/osascript "$HOME/bin/kiln-submit.js" "$1" --preset standard
   ```
   Make one workflow/setting per preset (`standard`, `4k`, `upscale`, `transcode-only`),
   differing only in the `--preset` value.

5. **Add the FCP Share Destinations.** Final Cut Pro → **File → Share → Add Destination** →
   double-click **Compressor Settings** → pick each preset. Name them **"kiln — Standard"**,
   **"kiln — 4K"**, **"kiln — Upscale"**, **"kiln — Transcode-only"**.

6. **End-to-end test.** In FCP, share a short timeline via **"kiln — Standard"**. Then on
   deb005 the user (or you, if you have access) can confirm it flowed through:
   `journalctl -u kiln` should show `job <id>: processing` → `job <id>: done (archived)`, and
   the outputs should land in the storage archive. The instant-ping (step below) is optional —
   without it the job still gets picked up within ~2 seconds.

## Config file to create (`~/.config/kiln/submit.json`)

```json
{
  "inbox": "/Volumes/kiln-inbox",
  "submit_url": "http://172.31.1.100:8765/submit",
  "default_preset": "standard"
}
```

- `inbox` — where the SMB share mounts (adjust if macOS mounts it elsewhere).
- `submit_url` — the LAN submit endpoint (now live, see above). If `deb005` resolves by name on
  the Mac, `http://deb005:8765/submit` also works. Set to `""` to disable the ping (jobs still
  get picked up within ~2s).
- `default_preset` — used when `--preset` is omitted.

## job.json contract (what the helper produces; deb005 consumes)

The helper builds this; you don't write it by hand, but this is what a job looks like:

```json
{
  "job_id": "2026-07-05_my-video",
  "source": "master.mov",
  "jobs": { "transcode": true, "captions": true, "normalize": true,
            "chapters": true, "metadata": true, "upscale": false },
  "options": { }
}
```

Presets set the `jobs` toggles. `options` is usually empty (deb005's config supplies defaults);
per-job overrides like `whisper_model`, `codec`, `target_lufs`, or `keep_master:true` (pin the
master against retention pruning) can be added via `--<name>` flags — see the spec §3.

## Rules to respect

- **No spaces** in any file/folder name the helper creates. (It sanitizes `job_id` to
  `[a-z0-9._-]`; keep that.)
- **Keep the atomic staging→rename** in `kiln-submit.js`. Do not "simplify" it to a direct copy
  into the inbox — that reintroduces the half-copied-job race the whole design avoids.
- **Compressor must not re-encode** (ProRes pass-through). Non-negotiable.
- If you change `kiln-submit.js`, keep it JXA/`osascript` (no Python — the system `python3` on
  a clean Mac prompts to install Xcode tools; that's why JXA was chosen).

## State of this repo / branch

- You are on branch **`mac-controller`** (pushed to GitHub at
  `github.com/jschwefel-CBB/kiln`). It contains the whole current project including this Mac
  work. `main` intentionally lags — the user pushes to `main` only after personally testing.
- The repo can also be pulled from deb005 directly over SSH:
  `git remote add deb005 ssh://jschwefel@172.31.1.100/home/jschwefel/repositories/kiln` then
  `git pull deb005 mac-controller`.
- **Note on deb005's live config:** the running deb005 has `[submit] host = "0.0.0.0"` (set
  live so the Mac ping works). The repo's shipped `packaging/config.deb005.toml` deliberately
  keeps the default `127.0.0.1` (localhost-only, the safe default for any fresh/public clone)
  with a comment explaining the `0.0.0.0` opt-in. This divergence is intentional — a public
  clone shouldn't get an all-interfaces endpoint unless the operator chooses it. Don't "fix"
  the repo default to match the live box.

When you hit a snag — especially an ObjC-bridge error from `kiln-submit.js` — that's the
expected rough edge. Fix the helper, re-run the two verification commands, then continue.
