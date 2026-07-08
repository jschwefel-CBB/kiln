# kiln on deb005 — Deployment Runbook

How to stand kiln up as a running systemd service on **deb005** (the RTX A4000 box),
from a fresh checkout to a service that processes a dropped master and archives it to
the storage server **ark**. Assumes no prior familiarity with systemd or Samba — every
command is spelled out for Debian.

> **Storage is already wired.** The storage server (`ark`) exports two NFS shares that are
> already mounted on deb005 at `/mnt/masters` and `/mnt/exports` (persistent in
> `/etc/fstab` with `_netdev,nofail`, verified writable). This runbook does **not**
> re-mount them — see [`storage-server-runbook.md`](storage-server-runbook.md) if you
> ever need to rebuild that. kiln simply archives to those two paths.

---

## 0. Prerequisites on deb005

kiln shells out to external tools. Confirm/obtain these before installing:

| Tool | Needed for | Check |
|---|---|---|
| Python 3.11+ | kiln itself | `python3 --version` |
| ffmpeg with NVENC | transcode, normalize, upscale-reassembly | `ffmpeg -hide_banner -encoders \| grep nvenc` |
| NVIDIA driver + `nvidia-smi` | GPU detection | `nvidia-smi` |
| ollama + a pulled model | metadata step | `ollama list` (expect `llama3.1:8b`) |
| CUDA 12 cuBLAS + cuDNN | **GPU** Whisper (else CPU fallback) | see §3 |
| realesrgan-ncnn-vulkan | opt-in upscale only | `command -v realesrgan-ncnn-vulkan` |

Pull the metadata model if missing:

```bash
ollama pull llama3.1:8b
```

---

## 1. Install the service

From the repo root on deb005:

```bash
# 1. Dry-run first — validates prerequisites and prints what it WOULD do, changing nothing.
./install.sh --check

# 2. Real install (creates the kiln user, /opt/kiln, the venv, the unit; runs kiln doctor).
sudo ./install.sh
```

What `install.sh` does (idempotent — safe to re-run):

1. Creates the system user **`kiln`** (no login shell) and adds it to the `video`,`render` groups (GPU access).
2. Creates `/opt/kiln` and `/var/content/kiln/{inbox,scratch,state}`, owned by `kiln`. The
   inbox/scratch/state base is `/var/content` (the Samsung 980 PRO — large + near-empty) so
   60–120 GB master drops and transcode scratch stay off the smaller boot fs; override with
   `STATE_BASE=... ./install.sh` on another host. All three must share one filesystem.
3. Copies the repo into `/opt/kiln`, builds `/opt/kiln/.venv`, and `pip install .[ai]` (kiln + faster-whisper).
4. Installs the starter config `packaging/config.deb005.toml` → `/opt/kiln/config.toml` (only if one isn't already there).
5. Installs and **enables** `kiln.service` (does not start it yet).
6. Runs `kiln doctor` as the `kiln` user.

`kiln doctor` should report the A4000, `/var/content/kiln/*` writable and on one filesystem,
and both `/mnt/masters` + `/mnt/exports` reachable.

Start it:

```bash
sudo systemctl start kiln
systemctl status kiln --no-pager        # expect: active (running)
journalctl -u kiln -f                    # follow the log
```

If it fails to start, `journalctl -u kiln -n 40 --no-pager` — the usual causes are a
config path typo or a mount permission; fix and `sudo systemctl restart kiln`.

---

## 2. Verify end-to-end (drop → archive to ark)

Drop the committed sample clip as a job and confirm it lands on ark:

```bash
sudo install -d -o kiln -g kiln /var/content/kiln/inbox/smoketest
sudo cp tests/fixtures/sample.mp4 /var/content/kiln/inbox/smoketest/master.mp4
echo '{"job_id":"smoketest","jobs":{"transcode":true,"normalize":true}}' \
  | sudo tee /var/content/kiln/inbox/smoketest/job.json >/dev/null
sudo chown -R kiln:kiln /var/content/kiln/inbox/smoketest

sleep 20   # let the watcher pick it up and process
ls -la /mnt/masters/smoketest/ /mnt/exports/smoketest/
```

Expected: `master.mp4` under `/mnt/masters/smoketest/`, `upload.mp4` under
`/mnt/exports/smoketest/`. Remove the test afterward:

```bash
sudo rm -rf /mnt/masters/smoketest /mnt/exports/smoketest
```

---

## 3. Enable GPU Whisper (CUDA 12 cuBLAS + cuDNN)

By default faster-whisper transcription **falls back to CPU** because CTranslate2
(faster-whisper's backend) needs **CUDA 12** cuBLAS + cuDNN on the library path, and a
stock box often has only the driver's / Ollama's private CUDA runtime. Transcode and
metadata already use the GPU; this only affects transcription speed.

**Recommended — let the installer do it.** Re-run the install with `--gpu-whisper`; it
installs the CUDA 12 wheels into the venv (~2.3 GB) and writes the `LD_LIBRARY_PATH`
drop-in for you:

```bash
sudo ./install.sh --gpu-whisper
sudo systemctl restart kiln
```

That is idempotent and safe to run over an existing install — it adds the libraries and
the drop-in without disturbing the rest.

<details>
<summary>What that does by hand (for reference)</summary>

```bash
# 1. Install the CUDA 12 backends into kiln's venv.
sudo /opt/kiln/.venv/bin/pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

# 2. Expose them to the service. The wheels land under
#    /opt/kiln/.venv/lib/python3.X/site-packages/nvidia/{cublas,cudnn}/lib.
#    Write a drop-in (keeps the packaged unit clean); match python3.X to the venv:
sudo install -d /etc/systemd/system/kiln.service.d
sudo tee /etc/systemd/system/kiln.service.d/10-cuda-libs.conf >/dev/null <<'EOF'
[Service]
Environment=LD_LIBRARY_PATH=/opt/kiln/.venv/lib/python3.13/site-packages/nvidia/cublas/lib:/opt/kiln/.venv/lib/python3.13/site-packages/nvidia/cudnn/lib
EOF
sudo systemctl daemon-reload
sudo systemctl restart kiln
```
</details>

Verify: run a job that requests captions/transcribe and watch `nvidia-smi` during the
run — the kiln process should appear, and the transcribe step's log message reports
`... on cuda` rather than `... on cpu`.

---

## 4. Samba: export `$INBOX` so the Mac can drop masters

The Mac drops FCP-exported masters into deb005's `$INBOX` over SMB (deb005 is the SMB
server for this hop; ark is unrelated to it). **Guest access is off** — the Mac
authenticates as a **dedicated, single-purpose drop user** (`kilndrop`), not a personal
login and not the `kiln` service account. That user exists only to own the SMB drop,
matching a strict credential-isolation posture.

The installer already makes the inbox group-writable + setgid (`chmod 2775`), so a member
of the `kiln` group can write it and everything created inherits group `kiln` (which the
service reads). You only need to create the drop user and give it an SMB password.

```bash
# 1. Install Samba (server) + smbclient (for the verification step below).
sudo apt update && sudo apt install -y samba smbclient

# 2. Add the share. Append packaging/smb-kiln-inbox.conf to the main config:
sudo tee -a /etc/samba/smb.conf < packaging/smb-kiln-inbox.conf

# 3. Create the dedicated drop user: no login shell, in the kiln group so it can write
#    the inbox. The stanza's `valid users = @kiln` then authorizes it.
sudo useradd --system --no-create-home --shell /usr/sbin/nologin -g kiln kilndrop

# 4. Give it an SMB password (interactive — the password never lands in a script or repo).
sudo smbpasswd -a kilndrop

# 5. Validate the config and restart.
testparm                       # should parse with no errors; shows [kiln-inbox]
sudo systemctl restart smbd
sudo systemctl enable smbd

# 6. Confirm the share is offered and the drop user can actually write it.
smbclient -L localhost -N | grep kiln-inbox        # share is listed
echo test > /tmp/smbtest.txt
smbclient //localhost/kiln-inbox -U kilndrop \
  -c 'put /tmp/smbtest.txt smbtest.txt; ls; del smbtest.txt'   # authenticated write round-trips
rm -f /tmp/smbtest.txt
```

**On the Mac:** Finder → **Go → Connect to Server** (⌘K) → `smb://deb005/kiln-inbox`
(or `smb://<deb005-ip>/kiln-inbox`) → **Registered User** with `kilndrop` + the SMB
password. **Not** guest. Export/copy a master in as `<job_id>/master.mov` alongside a
`job.json`; the running kiln service picks it up and archives it to ark.

> Wiring the FCP Share Destinations / `kiln-submit` helper that *writes* into this share
> is Phase 5 (the Mac controller spec). This runbook only stands up the deb005 server
> side of the drop.

---

## 5. Master retention (pruning old masters)

ProRes masters are huge; kiln can prune a master from the masters pool once it is older than
`retention_days`, keeping the compressed upload + artifacts forever on the exports pool.
`install.sh` installs and enables **`kiln-prune.timer`** (a daily sweep) — but the sweep
**deletes nothing until you opt in**.

**It is off by default.** To preview what would be pruned, changing nothing:

```bash
sudo -u kiln /opt/kiln/.venv/bin/kiln --config /opt/kiln/config.toml prune --dry-run
```

To actually enable pruning, set `prune_masters = true` in `/opt/kiln/config.toml` (adjust
`retention_days` as desired), then the daily timer will delete eligible masters. Verify the
timer:

```bash
systemctl status kiln-prune.timer --no-pager     # enabled/active
systemctl list-timers kiln-prune.timer --no-pager # next run time
journalctl -u kiln-prune.service --no-pager       # what the last sweep did
```

**Safety guarantees (why this won't lose data):**
- A master is deleted **only if** the exports pool is reachable **and** that job's
  `upload.mp4` + `result.json` (with `ok: true`) are present in `/mnt/exports/<job_id>/`.
  If the exports pool is offline, the **entire** sweep holds and deletes nothing.
- A job submitted with `options.keep_master: true` writes a `.keep_master` marker into its
  masters dir and is **never** pruned, regardless of age.
- With `prune_masters = false`, `kiln prune` reports would-prune candidates but deletes
  nothing — identical to `--dry-run`.

---

## Troubleshooting

- **`kiln doctor` says a `/var/content/kiln` path isn't writable** — re-run `sudo ./install.sh`; it fixes ownership.
- **doctor says inbox/scratch/state are on different filesystems** — they must share one filesystem, because the watcher enqueues a job by `os.rename`-ing its folder from inbox into `state/queued`, and `os.rename` cannot cross filesystems. The installer puts all three under `STATE_BASE` (`/var/content/kiln` on deb005 — the 980 PRO). If you relocate them, move the whole trio to one mount together; never split one off onto a different filesystem.
- **Archives show UNREACHABLE** — check the ark NFS mounts: `mount | grep /mnt/masters`. If absent, `sudo mount -a` (fstab has them). Jobs safely hold in `state/pending-archive/` until ark is back, then drain automatically.
- **Service won't start** — `journalctl -u kiln -n 40 --no-pager`. A missing `/opt/kiln/.venv/bin/kiln` means the install didn't finish; re-run it.
- **Mac SMB rejected** — a rejection at the password prompt means the user lacks an SMB password (`smbpasswd -a`); connects-but-can't-write means the user isn't in the `kiln` group / lacks write on the inbox. macOS caches failed logins — remove `deb005` from Keychain Access and retry.
