"""Intake: turns the founder brief into a structured summary."""
ROLE = "intake"
STEPS = ["phase1_step1"]
READS = {}     # step_key -> step keys to pass as digests; empty = catalogue default
SOURCES = {}   # step_key -> search query template ({idea}, {market}); empty = catalogue default
