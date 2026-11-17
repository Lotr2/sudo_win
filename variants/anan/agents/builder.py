"""Builder: MVP spec, the MVP itself (one call per feature), user docs.

User docs read only the spec (features are passed explicitly), so they run in parallel with the MVP.
"""
ROLE = "builder"
STEPS = ["phase3_step1", "phase3_step2", "phase3_step3"]
READS = {"phase3_step3": ["phase3_step1"]}
SOURCES = {}
