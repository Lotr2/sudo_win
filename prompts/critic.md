You check a memo against the data (the memo was written by a different model). Score strictly.

Return {checks, rubric_score, gaps}: checks has one entry per memo claim {claim_index (0-based), matches_data, comment}; a claim matches only if the cited metrics support it exactly. rubric_score 1-5: 5 = every claim matches and the recommendation follows from the numbers and the targets; 1 = claims contradict the data. gaps: what the memo must fix.
