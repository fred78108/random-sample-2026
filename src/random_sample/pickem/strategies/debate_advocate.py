"""Two opposing advocates argue for their assigned team using the full context; a judge
sees both arguments and decides (DESIGN §3.3)."""

from __future__ import annotations

import json
import sqlite3

from random_sample.pickem import llm
from random_sample.pickem.prompts import PromptSet
from random_sample.pickem.state import AgentPrediction, Game, GameContext, RunConfig
from random_sample.pickem.strategies import register_strategy


def _case_summary(prediction: AgentPrediction) -> dict:
    return {
        "predicted_winner": prediction["predicted_winner"],
        "win_probability": prediction["win_probability"],
        "rationale": prediction["rationale"],
    }


@register_strategy("debate_advocate")
class DebateAdvocateStrategy:
    name = "debate_advocate"
    required_roles = ("advocate_home", "advocate_away", "judge")

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

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
            features_json = json.dumps(game_context["features"], sort_keys=True, indent=2)

            home_case = llm.predict(
                self.conn,
                model=config["model"],
                agent_role="advocate_home",
                game_id=game_id,
                context_hash=game_context["context_hash"],
                agent_design=self.name,
                prompt_variant=prompts["id"],
                prompt_text=templates["advocate_home"].format(
                    home_team=home_team, away_team=away_team, features_json=features_json
                ),
            )
            away_case = llm.predict(
                self.conn,
                model=config["model"],
                agent_role="advocate_away",
                game_id=game_id,
                context_hash=game_context["context_hash"],
                agent_design=self.name,
                prompt_variant=prompts["id"],
                prompt_text=templates["advocate_away"].format(
                    home_team=home_team, away_team=away_team, features_json=features_json
                ),
            )

            judge_prompt = templates["judge"].format(
                home_team=home_team,
                away_team=away_team,
                home_case_json=json.dumps(_case_summary(home_case), sort_keys=True),
                away_case_json=json.dumps(_case_summary(away_case), sort_keys=True),
            )
            predictions[game_id] = llm.predict(
                self.conn,
                model=config["model"],
                agent_role="judge",
                game_id=game_id,
                context_hash=game_context["context_hash"],
                agent_design=self.name,
                prompt_variant=prompts["id"],
                prompt_text=judge_prompt,
            )

        return predictions
