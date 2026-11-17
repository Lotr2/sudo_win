Task: check a piece of text that a founder will read against the facts it was written from. You do not change any decision or outcome; you only make sure every statement is true to the facts.

You get `text`, `facts` (the only source of truth) and `what` (the kind of text).

Return {ok, problems, revised}:
- ok: true if every factual statement in `text` is supported by `facts`. Advice, suggestions and next steps are not factual statements: they are fine unless they contradict the facts.
- problems: each unsupported or overstated statement, quoted, with one short reason. Watch especially for: evidence described as stronger than it is (e.g. "posts from people in Cairo" when the facts say general articles, or "proven demand" from practice data), numbers that differ from the facts, outcomes stated differently from the recommendation, and anything invented about the founder or customers.
- revised: when ok is false, the full text rewritten so every statement is supported, keeping the same friendly tone, structure and length, and keeping all the advice that is fine. Change as little as possible. When ok is true, an empty string.
