"""Same three specialist calls as `specialist_synthesis`, but combined by a fixed
weighted-average in code instead of an LLM synthesis call — isolates whether synthesis
earns its cost (DESIGN §3.3)."""

from __future__ import annotations

import sqlite3

from random_sample.pickem.prompts import PromptSet
from random_sample.pickem.state import AgentPrediction, Game, GameContext, RunConfig
from random_sample.pickem.strategies import combine_by_average, register_strategy
from random_sample.pickem.strategies.specialist_synthesis import SpecialistCaller

# Equal weighting is a deliberate placeholder (DESIGN §10 decision #4 treats feature/
# combiner tuning as refined once the harness shows real results) — easy to bias toward
# one specialist later once backtest calibration data exists.
SPECIALIST_WEIGHTS = {"stats": 1.0, "situational": 1.0, "market": 1.0}


@register_strategy("specialist_deterministic")
class SpecialistDeterministicStrategy:
    name = "specialist_deterministic"
    required_roles = ("stats", "situational", "market")

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._caller = SpecialistCaller(conn, self.name)

    def predict(
        self,
        games: list[Game],
        context: dict[str, GameContext],
        config: RunConfig,
        prompts: PromptSet,
    ) -> dict[str, AgentPrediction]:
        templates = prompts["templates"]
        predictions: dict[str, AgentPrediction] = {}

        for game in games:
            game_id = game["game_id"]
            game_context = context[game_id]
            home_team, away_team = game["home_team"], game["away_team"]

            specialists = self._caller.call_all(
                templates, game_id, game_context["context_hash"], config["model"],
                prompts["id"], home_team, away_team, game_context["features"],
            )

            roles = ["stats", "situational", "market"]
            rationale = " | ".join(
                f"{role}: {specialists[role]['rationale']}" for role in roles
            )
            predictions[game_id] = combine_by_average(
                game_id=game_id,
                home_team=home_team,
                away_team=away_team,
                predictions=[specialists[role] for role in roles],
                weights=[SPECIALIST_WEIGHTS[role] for role in roles],
                agent_role="deterministic_combiner",
                rationale=rationale,
            )

        return predictions
