"""Experiment Designer (GPT-OSS-20B, high): proposes the demand test and its success target."""

from ..schemas import ExperimentProposal


def run(ctx, canvas: dict, risks: list[dict], floors: dict) -> ExperimentProposal:
    return ctx.router.call("experiment_designer", {"canvas": canvas, "riskiest_assumptions": risks,
                                                    "floors": floors}, ExperimentProposal, action="design_experiment")
