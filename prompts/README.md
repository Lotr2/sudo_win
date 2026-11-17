# Prompts

One file per agent. The router sends each agent `_shared.md` followed by its own file as the system prompt, with the task as JSON in the user message; every reply must match a JSON schema and is validated in code. The routing table is in `venture_builder/router.py`.

| File | Model | What the agent does |
|---|---|---|
| [`_shared.md`](_shared.md) | all agents | Rules every agent follows: JSON only, honesty, no income promises or fake trust, plain language for people |
| [`brief_parser.md`](brief_parser.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | Turns the founder's brief into a structured profile and lists what is missing |
| [`builder.md`](builder.md) | GPT-OSS-20B, medium effort | Fills the product template until the acceptance tests pass |
| [`canvas_builder.md`](canvas_builder.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | Writes the one-page business plan (Lean Canvas) |
| [`competitor_analyst.md`](competitor_analyst.md) | GPT-OSS-20B, low effort | Plans and refines searches about alternatives, prices and complaints; extracts graded findings |
| [`critic.md`](critic.md) | GPT-OSS-20B, high effort | Fact-checks the review against the numbers and the goals |
| [`docs_writer.md`](docs_writer.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | Writes the user guide and FAQ |
| [`experiment_designer.md`](experiment_designer.md) | GPT-OSS-20B, high effort | Designs the demand test and proposes a target before any result exists |
| [`fact_checker.md`](fact_checker.md) | GPT-OSS-20B, low effort | Checks explanations against their facts and corrects unsupported statements |
| [`founder_liaison.md`](founder_liaison.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | The founder's advisor: intake questions, read-back, kickoffs, stage walk-throughs, answers from the record |
| [`gate_reporter.md`](gate_reporter.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | Explains a stage check from the score breakdown and suggests other directions |
| [`landing_copywriter.md`](landing_copywriter.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | Writes the waitlist page copy and picks layout, section order and type style |
| [`launch_writers.md`](launch_writers.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | Drafts the store page and launch posts in the founder's own voice |
| [`market_researcher.md`](market_researcher.md) | GPT-OSS-20B, low effort | Plans and refines searches about the problem and the customers; extracts graded findings |
| [`mom_test_writer.md`](mom_test_writer.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | Writes a customer interview guide about past behaviour, not opinions |
| [`opportunity_scout.md`](opportunity_scout.md) | GPT-OSS-20B, medium effort | Writes many distinct versions of the idea for code to shortlist |
| [`pivot_memo_writer.md`](pivot_memo_writer.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | Writes the keep-going-or-change-course review from the results |
| [`policy_classifier.md`](policy_classifier.md) | the model that did not write the text | Second line of the Policy Guard: classifies text the rules didn't catch |
| [`policy_drafter.md`](policy_drafter.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | Drafts terms, privacy and refund policies (DRAFT: not legal advice) |
| [`pricing_analyst.md`](pricing_analyst.md) | GPT-OSS-20B, medium effort | Sets a price anchored on sourced competitor prices |
| [`product_manager.md`](product_manager.md) | GPT-OSS-20B, medium effort | Writes the product plan as machine-checked acceptance criteria |
| [`product_reviewer.md`](product_reviewer.md) | Gemma 4 31B (GPT-OSS-20B in single-model mode) | Scores the built product against a rubric and lists fixable issues |
| [`skeptical_investor.md`](skeptical_investor.md) | GPT-OSS-20B, high effort | Critiques the canvas on a rubric and asks the toughest questions |
| [`support_agent.md`](support_agent.md) | GPT-OSS-20B, low effort | Answers buyer questions only from the user docs; refunds go to the founder |

Every prompt tells the agent to cite evidence only by the IDs the tools created, to label unsupported claims `ASSUMPTION`, never to promise income, fake trust or urgency, or pose as anyone, and to write anything a person reads in plain words. The rules are also enforced in code (Policy Guard, Compliance Linter, Fact Checker, citation checks), so they never depend on a prompt alone.
