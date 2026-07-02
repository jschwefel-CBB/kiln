# Contributing to kiln

Thanks for your interest in `kiln`.

## License of contributions

`kiln` is licensed under the **Business Source License 1.1** (see [`LICENSE`](LICENSE)).
By submitting a contribution, you agree that your contribution is licensed under the same
terms and that the Licensor may relicense the project (including the eventual conversion to
MPL 2.0) as described in the license. If a Contributor License Agreement is introduced
later, contributions may be subject to it.

## Development setup

```bash
git clone <this-repo> kiln
cd kiln
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the test suite:

```bash
pytest
```

## Design

The authoritative design lives in [`docs/specs/`](docs/specs/). Read it before making
non-trivial changes. Key principles:

- **No hardcoded hardware or user-specific paths.** The reference machine is one *detected*
  configuration, not an assumption. Everything hardware-dependent goes through
  `kiln/hwprobe.py`; everything path-dependent goes through `config.toml`.
- **NVIDIA-only** GPU scope, but generation-aware (detect NVENC capabilities and VRAM at
  runtime; degrade gracefully; software fallback as a safety net).
- **Small, single-purpose modules** with a uniform step interface (`run(ctx) -> StepResult`).
- **Test-driven:** add or update tests alongside code. Pipeline steps are validated against
  the small sample clips in `tests/data/`.

## Style

- Python ≥ 3.11, type hints throughout.
- Keep functions focused; prefer clarity over cleverness.
- No secrets, no personal paths, and no `superpowers/` directories anywhere in the repo.
