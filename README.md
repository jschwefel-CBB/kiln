# kiln

**GPU-accelerated post-processing pipeline for video creators.** Drop a master onto a
GPU box, get back a compact upload file plus AI-generated captions, transcript, chapters,
and draft metadata — then everything is archived to your storage server automatically.

`kiln` is built for a two-machine workflow: edit on your workstation, hand the export to a
Linux box with an NVIDIA GPU, and let `kiln` do the heavy lifting (NVENC transcode, Whisper
transcription, loudness normalization, and local-LLM metadata drafting).

> **License:** `kiln` is **source-available** under the **Business Source License 1.1**
> (not an OSI "open source" license). **It is free for individuals and self-hosters.**
> A company offering `kiln` to third parties as a commercial hosted/managed service must
> obtain a commercial license. Each release converts to **MPL 2.0** four years after
> publication. See [`LICENSE`](LICENSE). For commercial licensing:
> `jschwefel@coldboreballisticsllc.com`.

---

## What it does

Given a video master and a job spec, `kiln` runs a configurable pipeline:

| Job | Description |
|-----|-------------|
| **transcode** | NVENC encode to a YouTube-ready MP4 (HEVC for 4K, H.264 High for ≤1080p; auto-selected). |
| **captions** | faster-whisper transcription → `.srt` captions + plain-text transcript. |
| **normalize** | Loudness-normalize audio to −14 LUFS (YouTube's target). |
| **chapters** | Topic-segment the transcript into YouTube chapter timestamps. |
| **metadata** | Local LLM (Ollama) drafts a description, title options, and tags. |
| **upscale** | *(opt-in)* Real-ESRGAN upscale/denoise for low-quality sources. |

Outputs are produced on local NVMe, then moved to your storage server. If storage is
offline, completed jobs wait in a local queue and are flushed when it returns.

## Hardware

- **GPU:** any modern **NVIDIA** card (≈8 GB VRAM and up). `kiln` detects your GPU's NVENC
  generation and VRAM at runtime and adapts — AV1 encode on Ada/RTX-40+ cards, HEVC/H.264
  elsewhere; Whisper model size is chosen to fit your VRAM. A software encode path is a
  safety-net fallback. (Intel/AMD GPUs are out of scope.)
- **OS:** Linux with a working NVIDIA driver + CUDA.

Run `kiln doctor` after install to see exactly what was detected and whether your storage
paths are reachable.

## Install (overview)

> Full, step-by-step install instructions live in `docs/` and the `install.sh` script.
> `kiln` runs as a `systemd` service from `/opt/kiln`.

**Prerequisites** (install these first — commands vary by distro):

- **NVIDIA driver + CUDA** (provides `nvidia-smi` and GPU compute).
- **ffmpeg with NVENC support** (`ffmpeg -encoders | grep nvenc` should list `hevc_nvenc`).
- **Python 3.11+**.
- **Ollama** (for the metadata job) — optional; the job is skippable.

Distro notes for prerequisites:

- **Debian / Ubuntu:** install the NVIDIA driver + CUDA per NVIDIA's `.deb` instructions;
  `sudo apt install ffmpeg python3 python3-venv`.
- **RHEL / Fedora:** enable RPM Fusion for `ffmpeg`; install NVIDIA/CUDA per NVIDIA's `.rpm`
  instructions; `sudo dnf install ffmpeg python3`.

Then clone this repo and run the installer:

```bash
git clone <this-repo> kiln
cd kiln
sudo ./install.sh        # creates a service user, installs to /opt/kiln, enables kiln.service
kiln doctor              # verify GPU detection + storage reachability
```

## Configuration

`kiln` reads a single `config.toml`. Copy the example and edit the three storage paths:

```bash
cp config.example.toml config.toml
```

```toml
# Where FCP / the Mac drops masters (local, exported over SMB). kiln watches this.
inbox = "/var/lib/kiln/inbox"

# Local working space (fast NVMe). Processing happens here.
scratch_dir = "/var/lib/kiln/scratch"

# Final archive destination — your storage server mount. Leave empty to hold jobs
# locally in the pending-archive queue until you set it.
archive = "/mnt/storage/kiln"   # e.g. an NFS or SMB mount; "" is valid
```

Changing where finished work lands is a one-line edit here — run `kiln doctor` to confirm
the new path is reachable and writable.

## Usage

```bash
kiln run /path/to/job-folder     # manually enqueue a job folder (master + job.json)
kiln status                      # show the processing queue, pending-archive queue, recent jobs
kiln doctor                      # detected GPU/VRAM/NVENC + storage checks
```

A job folder contains the master and a `job.json` describing which steps to run:

```json
{
  "job_id": "2026-07-01_my-video",
  "source": "my-video.mov",
  "title": "My Video Title",
  "jobs": { "transcode": true, "captions": true, "normalize": true,
            "chapters": true, "metadata": true, "upscale": false },
  "options": { "codec": "auto", "target_lufs": -14,
               "whisper_model": "large-v3", "llm_model": "llama3.1:8b" }
}
```

## Status

Early development. See [`docs/specs/`](docs/specs/) for the design spec and
[`CONTRIBUTING.md`](CONTRIBUTING.md) for how to work on it.
