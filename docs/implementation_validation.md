# Implementation validation

This document describes engineering verification. It does not report evidence of model
leakage, emergent communication, or collusion. No live LLM inference was used in these checks.

## Reproduce

Run from the repository root with Python 3.11 or newer:

```powershell
python -m unittest discover -s tests -v
python -m collusion validate
python -m collusion plan
python -m collusion run --backend mock --output runs/mock-validation
python -m collusion summarize runs/mock-validation
python scripts/check_secrets.py
```

Run outputs are local and Git-ignored. Reusing the same output directory resumes cached
calls; use a new directory for a fresh execution. The secret check inspects staged Git
content, not ignored local credentials.

## Verification coverage

The 14 automated tests cover:

- Exhaustive finite-state balance, channel capacity, public/private independence, and
  reproducibility with a seed unrelated to the date.
- Sender information separation and rejection of reports outside the permitted language.
- Scoring of correct, incorrect, abstaining, malformed, and duplicate-key outputs.
- OpenRouter request construction, parameter checks, and rejection of unavailable models.
- Call budgets, cache reuse, configuration mismatch detection, interrupted-run recovery,
  and replacement of invalid sender messages before delivery.

Environment validation checks all 96 states. An offline full mock execution completed
1,248 logical calls and produced 672 condition rows. Mock senders return public-only text;
the mock receiver abstains except for the full-record lookup. These outputs exercise the
pipeline rather than estimate any model's behavior.

GitHub Actions runs the tests, environment validation, staged-content secret scan, and a
two-state mock smoke test on Python 3.11 and 3.12. The full mock sweep is a separate local
check. Bootstrap seed is 43; world seed is 17.

## Limits

Provider tests use simulated responses. A public model-catalog preflight can check an exact
model identifier and advertised parameters, but cannot establish account access, billing,
inference quality, or checkpoint stability. Real inference requires an explicitly selected
backend and local credentials. Actual model/provider identifiers and available usage data
are recorded for each successful response.

Offline validation does not establish a safety result. Bootstrap intervals are descriptive
over paired worlds, not evidence from independent repeated model runs. Partial smoke tests
do not preserve the balancing of the exhaustive sweep. A timed-out request can have an
unknown billing outcome even if no completion was received.
