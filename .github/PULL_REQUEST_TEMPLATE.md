<!-- Thanks for the PR! Keep this template — reviewers will fill in what's missing. -->

## Summary
<!-- One paragraph: what does this change and why? -->

## Related issues
Closes #

## Type of change
- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (migration required)
- [ ] Documentation only
- [ ] Refactor (no functional change)
- [ ] Test-only
- [ ] Infra / CI / tooling

## How I tested this
<!-- Commands run, screenshots, render outputs. -->

```
$ pytest tests/unit/test_xxx.py
```

## Checklist
- [ ] I ran `ruff check .` and `ruff format --check .`
- [ ] I ran `pytest` (or explained why it wasn't relevant)
- [ ] New code has at least one test (unit or integration)
- [ ] Public functions have docstrings
- [ ] I updated CHANGELOG.md if this affects users
- [ ] Secrets aren't hardcoded (checked `.env.example` if new vars added)
- [ ] If touching the VPS or production path, I noted the rollback plan below

## Rollback plan
<!-- If this breaks in production, how do we revert? (e.g. "revert commit SHA, redeploy") -->

## Screenshots / video (if UI or visual change)

## Deployment notes
<!-- Anything reviewers should know about shipping this. -->
