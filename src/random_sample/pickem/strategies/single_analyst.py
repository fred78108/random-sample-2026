"""One LLM call per game, full context. Baseline agent design (DESIGN §3.3)."""

from __future__ import annotations

import json
import sqlite3

from random_sample.pickem import llm
from random_sample.pickem.prompts import PromptSet
from random_sample.pickem.state import AgentPrediction, Game, GameContext, RunConfig
from random_sample.pickem.strategies import register_strategy


@register_strategy("single_analyst")
class SingleAnalystStrategy:
    name = "single_analyst"
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
            game_context = context[game["game_id"]]
            prompt_text = template.format(
                home_team=game["home_team"],
                away_team=game["away_team"],
                features_json=json.dumps(game_context["features"], sort_keys=True, indent=2),
            )
            predictions[game["game_id"]] = llm.predict(
                self.conn,
                model=config["model"],
                agent_role="analyst",
                game_id=game["game_id"],
                context_hash=game_context["context_hash"],
                agent_design=self.name,
                prompt_variant=prompts["id"],
                prompt_text=prompt_text,
            )
        return predictions
