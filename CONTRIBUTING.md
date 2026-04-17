# Contributing

Thanks for working on peanut-reacts-pipeline. This doc is the short version of how we work.

---

## TL;DR

1. Branch from `master` — name it `type/short-description` (e.g. `fix/reddit-403`, `feat/auto-thumbnails`).
2. Write code + at least one test.
3. `ruff check . && ruff format . && pytest tests/unit`.
4. Open a PR, fill the template, tag yourself + link the issue.
5. Merge via squash. Delete branch.

---

## Dev setup

```bash
# First time
git clone https://github.com/AnasPine2000/peanut-reacts-pipeline.git
cd peanut-reacts-pipeline
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install --upgrade pip wheel
pip install -e ".[dev]"        # installs the package + pytest, ruff, mypy

# Sanity check
ruff check . && pytest tests/unit
```

The heavy ML deps (torch, whisper, SadTalker) aren't in `[dev]` — they're pulled by `pip install -e .` alone. In CI we skip them to keep workflows fast.

## Branching & commits

- **Branch names**: `type/short-description`. Allowed types: `feat`, `fix`, `chore`, `docs`, `refactor`, `perf`, `test`, `infra`, `release`.
- **Commits**: Conventional Commits style preferred.
  - `feat(reddit): add PRAW OAuth fallback`
  - `fix(sadtalker): preserve alpha after chroma-key scale`
  - `docs(plan): update M3 view targets`
- Small, focused commits beat giant end-of-week dumps.

## PR expectations

- Fill in the PR template. Reviewers will bounce incomplete ones.
- Every PR should have:
  - A linked issue (or a sentence on why no issue is needed)
  - At least one test — or a written reason why tests aren't feasible
  - Passing CI (lint + unit tests)
  - No secrets, no hardcoded paths to a specific machine
- Squash-merge into `master`. Delete the branch after.

## Testing

```bash
# Unit tests — fast, no external services
pytest tests/unit

# Full test suite including integration (still mocked)
pytest

# Single file
pytest tests/unit/test_reddit_scraper.py -v

# Only tests matching a name
pytest -k "test_truncate"

# Mark selection
pytest -m "not slow and not gpu"
```

Test categories (markers):
- `slow` — takes more than ~5s; excluded from default CI
- `integration` — end-to-end with mocks; opt-in via `-m integration`
- `gpu` — needs CUDA (SadTalker, Hallo2); only runs on machines with GPU
- `benchmark` — perf tests; opt-in via `-m benchmark`

## Lint + format

```bash
ruff check .            # lint
ruff format .           # auto-format
ruff check . --fix      # auto-fix what's safe
```

We use `ruff` as the single tool replacing `black + isort + flake8`. Config in `pyproject.toml`.

## Type checking

```bash
mypy src/peanut_reacts
```

Gradual typing — new code should have type hints on public functions. We don't block PRs on missing types yet.

## Before opening a PR

- [ ] Tests pass (`pytest tests/unit`)
- [ ] Ruff clean (`ruff check . && ruff format --check .`)
- [ ] No `.env`, `*token*`, `client_secret*`, or similar in the diff
- [ ] If you added a new API (env var, config key, CLI flag), updated `.env.example` / README
- [ ] If touching the VPS path, you know how to roll it back
- [ ] Large files (videos, audio, models) go in `.gitignore`, not the repo

## Releases

We don't have releases yet — everything ships straight from `master` on merge. When we do:
- Tag commits with `vX.Y.Z` (semver)
- Update CHANGELOG.md

## Getting help

- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- Runbook: [docs/runbook.md](docs/runbook.md) *(coming)*
- Strategy: [EXECUTION_PLAN_V2.md](EXECUTION_PLAN_V2.md)
- Ping the owner (see CODEOWNERS) for anything urgent
