# Git hooks

Opt-in client-side hooks. To activate in this clone:

```bash
git config core.hooksPath .githooks
```

## `pre-push`

Scans both the tracked working tree and the commits being pushed for
hard-fail patterns (AWS account ids, AKIA keys, sk_ API keys, PEM
private keys, personal emails, personal names, Co-Authored-By trailers).

Bypass in an emergency only: `git push --no-verify`.
