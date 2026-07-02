"""Tests for kiln.steps.transcribe — model/device resolution, SRT formatting, mocked run."""

from __future__ import annotations

from dataclasses import dataclass

from kiln.steps import transcribe


def test_resolve_model_auto_uses_tier(fake_hardware) -> None:
    assert transcribe.resolve_model("auto", fake_hardware(vram_mb=16376)) == "large-v3"
    assert transcribe.resolve_model("auto", fake_hardware(vram_mb=1500)) == "base"


def test_resolve_model_explicit_passthrough(fake_hardware) -> None:
    assert transcribe.resolve_model("small", fake_hardware()) == "small"


def test_resolve_device(fake_hardware) -> None:
    assert transcribe.resolve_device(fake_hardware(cuda_available=True)) == ("cuda", "float16")
    assert transcribe.resolve_device(fake_hardware(cuda_available=False)) == ("cpu", "int8")


@dataclass
class _Seg:
    start: float
    end: float
    text: str


def test_segments_to_srt_format() -> None:
    srt = transcribe._segments_to_srt([_Seg(0.0, 1.5, " Hello"), _Seg(1.5, 3.0, " world ")])
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello\n" in srt
    assert "2\n00:00:01,500 --> 00:00:03,000\nworld\n" in srt


def test_run_writes_srt_and_transcript_mocked(step_ctx, monkeypatch) -> None:
    # Patch WhisperModel so the test never downloads a model or touches the GPU.
    class _FakeModel:
        def __init__(self, *a, **k): ...
        def transcribe(self, path):
            segs = [_Seg(0.0, 1.0, " hello"), _Seg(1.0, 2.0, " there")]
            return iter(segs), {"language": "en"}

    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeModel, raising=True)

    ctx = step_ctx()
    result = transcribe.run(ctx)
    assert result.ok is True, result.message
    srt = (ctx.workdir / "captions.srt").read_text()
    txt = (ctx.workdir / "transcript.txt").read_text()
    assert "00:00:00,000 --> 00:00:01,000" in srt
    assert "hello" in txt and "there" in txt
