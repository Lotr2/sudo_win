"""Researcher: sourced customer pains, competitors and competitor prices.

Queries name the target market as well as the idea, which finds more first-hand complaints on HN/Wikipedia.
"""
ROLE = "researcher"
STEPS = ["phase1_step2", "phase1_step4"]
READS = {}
SOURCES = {
    "phase1_step2": "{market} problems {idea}",
    "phase1_step4": "{idea} alternatives price",
}
