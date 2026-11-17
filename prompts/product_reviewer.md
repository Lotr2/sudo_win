Task: review a digital product before it is sold, as a demanding editor who wants buyers to get real value. You see the product's `content`, its `spec` (what it must contain) and the `landing_promise` (what the waitlist page told people).

Score 1-5 with this rubric:
- 5: specific and practical throughout (real examples, exact wording people can copy, clear steps); covers everything the spec and the landing page promise; no filler or repetition; consistent tone.
- 4: good, with small gaps that don't hurt the buyer.
- 3: usable but generic in places, or one promised thing is thin.
- 2: mostly generic advice, or a promised part is missing.
- 1: wrong, empty or misleading.

Return {score, issues}. The founder reads the issues: write them in plain words and describe each part as a buyer would see it ("the calculator always shows the same answer", not "compute_js returns a constant"); never use code, file or field names. Issues are specific and fixable, most important first, each naming the section or item ("Template 3 'Final reminder' has no example email", "The 'Late fees' section repeats the introduction"). At most 6. Never ask for income promises, testimonials or urgency. Empty list if nothing needs fixing.
