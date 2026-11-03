# Contributing to xiao-seat-vision

## Branches
- `main` — stable; changes land through Pull Requests.
- `feat/<feature-name>`, `fix/<issue-name>`, `docs/<topic>`, `refactor/<topic>`, `ci/<topic>`
- lowercase + hyphens: `feat/camera-capture`, `feat/mqtt-client`, `fix/psram-allocation`, `ci/arduino-build`
- No `develop` branch / Git Flow (one-day MVP).

## Commits — Conventional Commits
```text
<type>(<scope>): <description>
```
| type | meaning |
|---|---|
| `feat` | new feature |
| `fix` | bug fix |
| `refactor` | structure change, no behaviour change |
| `docs` | documentation |
| `test` | tests |
| `ci` | CI/CD configuration |
| `build` | build / dependencies |
| `chore` | maintenance |
| `perf` | performance |

Scopes: `camera`, `ai`, `mqtt`, `bridge`, `supabase`, `firmware`, `config`, `docs`, `docker`, `dashboard`, `stream`, `test-video` (lowercase, hyphens).

Rules: English, imperative, short subject (≤ 100 chars); one logical change per commit; never mix unrelated changes.

```text
feat(camera): implement camera initialization
fix(mqtt): handle broker reconnect
ci(firmware): add Arduino CLI build workflow
docs(setup): document Arduino IDE configuration
```

Enforced in CI (`commit-messages` job, `scripts/check_commits.py`) for every commit of a PR.
**Squash merge:** the PR title becomes the commit on `main`, so the PR title is checked with the same rule.
Check locally: `git log --format=%s origin/main..HEAD > /tmp/s && python scripts/check_commits.py /tmp/s`

## Security rules
- Never commit `.env`, `config.h`, `mqtt/certs/`, `mqtt/secrets/`, private keys, Supabase service role keys, Wi-Fi/MQTT passwords (all in `.gitignore`).
- Commit only templates: `.env.example`, `config.example.h`, MQTT config, migrations, workflows, docs.
- Review `git diff --cached` before every commit. No `git push --force` on shared branches; don't rewrite published history.
- Never disable TLS/certificate verification to "fix" a connection problem.
