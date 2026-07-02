#!/usr/bin/env bash
# Generates the tiny synthetic test clip committed as sample.mp4.
# Regenerate with: bash tests/fixtures/make_fixture.sh
# ~3s, 320x240, H.264 + AAC sine tone — a few hundred KB, no copyright, no real speech.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ffmpeg -y \
  -f lavfi -i "testsrc=size=320x240:rate=30:duration=3" \
  -f lavfi -i "sine=frequency=440:duration=3" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
  "$here/sample.mp4"
echo "wrote $here/sample.mp4 ($(du -h "$here/sample.mp4" | cut -f1))"
