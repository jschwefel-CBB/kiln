"""Tests for kiln.steps.chapters — deterministic transcript segmentation."""

from __future__ import annotations

from kiln.steps import chapters


def test_build_chapters_starts_at_zero() -> None:
    text = "\n".join(f"line {i} words here" for i in range(20))
    out = chapters.build_chapters(text, max_chapters=4)
    assert out[0].startswith("0:00 ")
    assert len(out) <= 4
    assert all(":" in line for line in out)  # each has a timestamp


def test_build_chapters_empty_transcript() -> None:
    out = chapters.build_chapters("", max_chapters=8)
    # Always at least the opener.
    assert out == ["0:00 Intro"]


def test_run_without_transcript_fails(step_ctx) -> None:
    ctx = step_ctx()
    result = chapters.run(ctx)
    assert result.ok is False
    assert "transcript" in result.message.lower()


def test_run_writes_chapters(step_ctx) -> None:
    ctx = step_ctx()
    (ctx.workdir / "transcript.txt").write_text(
        "\n".join(f"sentence number {i} about a topic" for i in range(30))
    )
    result = chapters.run(ctx)
    assert result.ok is True, result.message
    lines = (ctx.workdir / "chapters.txt").read_text().splitlines()
    assert lines and lines[0].startswith("0:00 ")
