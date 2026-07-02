"""Tests for kiln.steps.metadata — prompt building + mocked Ollama generation."""

from __future__ import annotations

from kiln.steps import metadata


def test_build_prompt_includes_transcript_and_asks_for_fields() -> None:
    p = metadata.build_prompt("a talk about lens ballistics")
    assert "lens ballistics" in p
    low = p.lower()
    assert "description" in low and "title" in low and "tag" in low


def test_run_without_transcript_fails(step_ctx) -> None:
    ctx = step_ctx()
    result = metadata.run(ctx)
    assert result.ok is False
    assert "transcript" in result.message.lower()


def test_run_writes_metadata_mocked(step_ctx, monkeypatch) -> None:
    ctx = step_ctx()
    (ctx.workdir / "transcript.txt").write_text("hello world, this is a test video")
    monkeypatch.setattr(
        metadata, "_ollama_generate",
        lambda *a, **k: "## Description\nGreat video.\n\n## Titles\n- One\n\n## Tags\ntag1, tag2",
    )
    result = metadata.run(ctx)
    assert result.ok is True, result.message
    md = (ctx.workdir / "metadata.md").read_text()
    assert "Description" in md and "Titles" in md


def test_run_reports_ollama_failure(step_ctx, monkeypatch) -> None:
    ctx = step_ctx()
    (ctx.workdir / "transcript.txt").write_text("x")

    def _boom(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(metadata, "_ollama_generate", _boom)
    result = metadata.run(ctx)
    assert result.ok is False
    assert "ollama" in result.message.lower() or "connection" in result.message.lower()
