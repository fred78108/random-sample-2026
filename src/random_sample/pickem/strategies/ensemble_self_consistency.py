"""Reuses the `single_analyst` prompt ("analyst" role), sampled N times and
averaged/voted in code (DESIGN §3.3)."""

from __future__ import annotations

import json
import sqlite3

from random_sample.pickem import llm
from random_sample.pickem.prompts import PromptSet
from random_sample.pickem.state import AgentPrediction, Game, GameContext, RunConfig
from random_sample.pickem.strategies import combine_by_average, register_strategy

# Not part of RunConfig's grid (DESIGN §3.1 has no ensemble-size field) — a fixed
# placeholder small enough to keep a backtest sweep affordable, per DESIGN §10 decision #4.
ENSEMBLE_SIZE = 5


@register_strategy("ensemble_self_consistency")
class EnsembleSelfConsistencyStrategy:
    name = "ensemble_self_consistency"
    required_roles = ("analyst",)

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def predict(
        self,
        games: list[Game],
        context: dict[str, GameContext],
        config: RunConfig,
        prompts: PromptSet,
    ) -> dict[str, AgentPrediction]:
        template = prompts["templates"]["analyst"]
        predictions: dict[str, AgentPrediction] = {}

        for game in games:
            game_id = game["game_id"]
            game_context = context[game_id]
            home_team, away_team = game["home_team"], game["away_team"]
            prompt_text = template.format(
                home_team=home_team,
                away_team=away_team,
                features_json=json.dumps(game_context["features"], sort_keys=True, indent=2),
            )

            # Same prompt text every sample, but `agent_role` is tagged per-sample so each
            # draws its own llm_cache entry — otherwise the cache key (which doesn't
            # include a sample index) would collapse all N calls onto a single cached
            # response and defeat the whole point of sampling repeatedly.
            samples = [
                llm.predict(
                    self.conn,
                    model=config["model"],
                    agent_role=f"analyst_sample_{i}",
                    game_id=game_id,
                    context_hash=game_context["context_hash"],
                    agent_design=self.name,
                    prompt_variant=prompts["id"],
                    prompt_text=prompt_text,
                )
                for i in range(ENSEMBLE_SIZE)
            ]

            winners = [s["predicted_winner"] for s in samples]
            rationale = (
                f"Ensemble of {ENSEMBLE_SIZE} samples ({winners.count(home_team)} for "
                f"{home_team}, {winners.count(away_team)} for {away_team})."
            )
            predictions[game_id] = combine_by_average(
                game_id=game_id,
                home_team=home_team,
                away_team=away_team,
                predictions=samples,
                agent_role="ensemble_combiner",
                rationale=rationale,
            )

        return predictions
