# Security policy

## Threat model

This repo automates uploads to YouTube + TikTok channels and holds API keys for DeepSeek,
ElevenLabs, and the YouTube Data API. The primary risks are:

1. **Credential leak** — committing a `.env`, OAuth token, or `client_secret.json`.
2. **OAuth token theft** — a compromised VPS could post unauthorized videos.
3. **Supply chain** — a malicious PyPI package in dependencies.
4. **Content-ID / copyright violation** — not strictly security, but legal risk.

## What's never in the repo

- `.env` (gitignored)
- `client_secret*.json` (gitignored)
- `*token*.json` (gitignored)
- `cookies*.txt` (gitignored)
- `*.db` (gitignored — contains scraped content + state)
- Any file under `/tokens/` or `/secrets/`

If you find a secret in a commit, **even in history**, please:
1. Immediately rotate the leaked secret (Google OAuth: revoke tokens; API keys: regenerate).
2. Open a private issue or DM the owner.
3. Don't publicly post the SHA or file path until rotation is confirmed.

## Reporting a vulnerability

For now (single-owner project), email the owner directly or open a private issue.
Don't file public issues for anything that could be exploited before a fix lands.

## What we do to reduce risk

- Dependabot weekly PRs for dependency updates (`.github/dependabot.yml`).
- `ruff` lint blocks obvious mistakes (eval, bare except, hardcoded secrets patterns).
- CI runs with zero secrets — integration tests use mocks so keys never leave the dev machine or VPS.
- CODEOWNERS review required for changes to `/deploy`, `/channels.yaml`, `/.github/`.
- `.gitignore` covers the usual suspects; also has lines for machine-specific tokens.

## What's on our roadmap

- [ ] Pre-commit hook that scans staged changes with `gitleaks` or `trufflehog`
- [ ] CI secret scanning via GitHub's built-in secret-scanning (native on public repos)
- [ ] Signed commits / tags (`git commit -S`)
- [ ] SBOM generation on release (when we start cutting releases)
- [ ] Dependabot auto-merge for low-risk patch updates
