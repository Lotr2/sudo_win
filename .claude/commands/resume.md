---
description: Resume team work on sudo_win - pick your person, see your tasks and your variant's contract status
---

Resume work for one team member. Do not change any files while running this command.

1. If "$ARGUMENTS" names one of anan, ziad, aly, mostafa, use it. Otherwise ask exactly: "Which person are you? (anan, ziad, aly, mostafa)" and wait for the answer.
2. Read `.planning/TEAM.md` and `.planning/PROGRESS.md` (run `git pull --rebase` first if the working tree is clean). Show:
   - the shared-core files this person owns;
   - their open tasks from PROGRESS.md (`[ ]` and `[~]`);
   - the next team milestone and how long until it, from the timeline in TEAM.md.
3. If `variants/<person>/` does not exist, say so and show their starter prompt from TEAM.md (it begins by copying `variants/_template/`).
4. Otherwise run the contract test with the project's Python (`.venv` if present):
   `python -m pytest tests/test_variant_contract.py -k <person> -q`
   Report pass/fail and the first failure message, if any.
5. If `.planning/COMPARISON.md` has a row for this variant, show it.
6. Remind them: change only your variant folder and the shared files you own; never edit `CLAUDE.md`; `git pull --rebase` before pushing to `main`.
