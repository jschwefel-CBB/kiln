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
2. Creates `/opt/kiln` and `/var/lib/kiln/{inbox,scratch,state}`, owned by `kiln`.
3. Copies the repo into `/opt/kiln`, builds `/opt/kiln/.venv`, and `pip install .[ai]` (kiln + faster-whisper).
4. Installs the starter config `packaging/config.deb005.toml` → `/opt/kiln/config.toml` (only if one isn't already there).
5. Installs and **enables** `kiln.service` (does not start it yet).
6. Runs `kiln doctor` as the `kiln` user.

`kiln doctor` should report the A4000, `/var/lib/kiln/*` writable and on one filesystem,
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
sudo install -d -o kiln -g kiln /var/lib/kiln/inbox/smoketest
sudo cp tests/fixtures/sample.mp4 /var/lib/kiln/inbox/smoketest/master.mp4
echo '{"job_id":"smoketest","jobs":{"transcode":true,"normalize":true}}' \
  | sudo tee /var/lib/kiln/inbox/smoketest/job.json >/dev/null
sudo chown -R kiln:kiln /var/lib/kiln/inbox/smoketest

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

By default faster-whisper transcription **falls back to CPU** on deb005 because the box
has only Ollama's private cuBLAS (CUDA 13) — CTranslate2 (faster-whisper's backend) needs
**CUDA 12** cuBLAS + cuDNN, which aren't on the library path. Transcode/metadata already
use the GPU; this only affects transcription speed.

Simplest fix — install the CUDA 12 libraries into kiln's venv:

```bash
sudo /opt/kiln/.venv/bin/pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Then make them discoverable to the service. The wheels land under
`/opt/kiln/.venv/lib/python3.*/site-packages/nvidia/*/lib`. Add an
`Environment=LD_LIBRARY_PATH=...` line to the unit (a drop-in keeps the packaged unit
clean):

```bash
sudo systemctl edit kiln
# in the editor, add (adjust the python3.x version to match the venv):
#   [Service]
#   Environment=LD_LIBRARY_PATH=/opt/kiln/.venv/lib/python3.13/site-packages/nvidia/cublas/lib:/opt/kiln/.venv/lib/python3.13/site-packages/nvidia/cudnn/lib
sudo systemctl daemon-reload
sudo systemctl restart kiln
```

Verify: run a job that requests captions/transcribe and watch `nvidia-smi` during the
run — the kiln process should appear, and the transcribe step's log message reports
`... on cuda` rather than `... on cpu`.

---

## 4. Samba: export `$INBOX` so the Mac can drop masters

The Mac drops FCP-exported masters into deb005's `$INBOX` over SMB (deb005 is the SMB
server for this hop; ark is unrelated to it). **Guest access is off** — use a real user,
same policy as ark.

```bash
# 1. Install Samba.
sudo apt update && sudo apt install -y samba

# 2. Add the share. Append packaging/smb-kiln-inbox.conf to the main config:
sudo tee -a /etc/samba/smb.conf < packaging/smb-kiln-inbox.conf

# 3. Give a real system user an SMB password (the user must exist and be able to write
#    /var/lib/kiln/inbox — put it in the kiln group). Replace <user>:
sudo usermod -aG kiln <user>
sudo smbpasswd -a <user>

# 4. Validate the config and restart.
testparm                       # should parse with no errors; shows [kiln-inbox]
sudo systemctl restart smbd
sudo systemctl enable smbd

# 5. Confirm the share is offered.
smbclient -L localhost -U <user>     # enter the SMB password; expect kiln-inbox listed
```

**On the Mac:** Finder → **Go → Connect to Server** (⌘K) → `smb://deb005/kiln-inbox`
(or `smb://<deb005-ip>/kiln-inbox`) → **Registered User** with `<user>` + the SMB
password. **Not** guest. Export/copy a master in as `<job_id>/master.mov` alongside a
`job.json`; the running kiln service picks it up and archives it to ark.

> Wiring the FCP Share Destinations / `kiln-submit` helper that *writes* into this share
> is Phase 5 (the Mac controller spec). This runbook only stands up the deb005 server
> side of the drop.

---

## Troubleshooting

- **`kiln doctor` says a `/var/lib/kiln` path isn't writable** — re-run `sudo ./install.sh`; it fixes ownership.
- **doctor says inbox/scratch/state are on different filesystems** — they must share one; the installer puts them all under `/var/lib/kiln` on `/`. Don't relocate one onto a different mount.
- **Archives show UNREACHABLE** — check the ark NFS mounts: `mount | grep /mnt/masters`. If absent, `sudo mount -a` (fstab has them). Jobs safely hold in `state/pending-archive/` until ark is back, then drain automatically.
- **Service won't start** — `journalctl -u kiln -n 40 --no-pager`. A missing `/opt/kiln/.venv/bin/kiln` means the install didn't finish; re-run it.
- **Mac SMB rejected** — a rejection at the password prompt means the user lacks an SMB password (`smbpasswd -a`); connects-but-can't-write means the user isn't in the `kiln` group / lacks write on the inbox. macOS caches failed logins — remove `deb005` from Keychain Access and retry.
