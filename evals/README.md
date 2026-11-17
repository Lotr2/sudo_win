# Evals

Briefs with the Stage 1 outcome we expect, plus checks that everything the founder reads is free of internal codes,
raw data and unexplained scores. Run them after changing a prompt, a skill or a threshold.

```bash
python evals/run_evals.py                 # real models and live search, demo mode
python evals/run_evals.py --only crypto_signals,fake_reviews
python evals/run_evals.py --fake          # the test fakes: checks the harness (also run by tests/test_evals.py)
python evals/run_evals.py --save          # also writes evals/results.md from a real run
```

Each case runs until its first stage decision. Nothing is approved or decided on the founder's behalf: questions
time out as in demo mode. Cases are in `cases.json`; briefs in `briefs/` (and two from the repository's `briefs/`).
Requests that should be refused (fake reviews, excluded products) are kept here, not in `docs/`.
