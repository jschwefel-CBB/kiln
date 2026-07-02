# Test media

This directory holds small sample clips used by the pipeline step tests (Phase 3).

**Rules:**
- Keep every committed sample **under 5 MB** (large media belongs outside git).
- Include at least one **≤1080p** and one **4K** short clip so the transcode step's
  auto-codec selection (H.264 vs HEVC) can be exercised.
- Generated outputs (transcoded files, `.srt`, etc.) are written to `_out/` / `scratch/`,
  which are git-ignored — never commit them.

Sample clips are added in Phase 3 alongside the step implementations.
