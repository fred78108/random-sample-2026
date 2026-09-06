"""Fan out stats/situational/market specialist calls, each seeing only its own data slice,
then an LLM synthesis call combines them (DESIGN §3.3)."""

from __future__ import annotations

import json
import sqlite3

from random_sample.pickem import llm
from random_sample.pickem.prompts import PromptSet
from random_sample.pickem.state import AgentPrediction, Game, GameContext, RunConfig
from random_sample.pickem.strategies import register_strategy

# Everything in a merged features dict that isn't the "situational"/"market"/"head_to_head"/
# injury sub-slices belongs to the "stats" role's own view (DESIGN §3.3's role mapping).
_NON_STATS_KEYS = {"situational", "market", "head_to_head", "home_injuries", "away_injuries"}


def _stats_slice(features: dict) -> dict:
    return {k: v for k, v in features.items() if k not in _NON_STATS_KEYS}


def _prediction_summary(prediction: AgentPrediction) -> dict:
    return {
        "predicted_winner": prediction["predicted_winner"],
        "win_probability": prediction["win_probability"],
        "rationale": prediction["rationale"],
    }


class SpecialistCaller:
    """Shared specialist fan-out, reused by `specialist_deterministic` (DESIGN §3.3 notes
    the two strategies share the same three specialist calls)."""

    def __init__(self, conn: sqlite3.Connection, strategy_name: str):
        self.conn = conn
        self.strategy_name = strategy_name

    def call(
        self,
        role: str,
        template: str,
        game_id: str,
        context_hash: str,
        model: str,
        prompt_variant: str,
        home_team: str,
        away_team: str,
        features_slice: dict,
    ) -> AgentPrediction:
        prompt_text = template.format(
            home_team=home_team,
            away_team=away_team,
            features_json=json.dumps(features_slice, sort_keys=True, indent=2),
        )
        return llm.predict(
            self.conn,
            model=model,
            agent_role=role,
            game_id=game_id,
            context_hash=context_hash,
            agent_design=self.strategy_name,
            prompt_variant=prompt_variant,
            prompt_text=prompt_text,
        )

    def call_all(
        self,
        templates: dict[str, str],
        game_id: str,
        context_hash: str,
        model: str,
        prompt_variant: str,
        home_team: str,
        away_team: str,
        features: dict,
    ) -> dict[str, AgentPrediction]:
        return {
            "stats": self.call(
                "stats", templates["stats"], game_id, context_hash, model, prompt_variant,
                home_team, away_team, _stats_slice(features),
            ),
            "situational": self.call(
                "situational", templates["situational"], game_id, context_hash, model,
                prompt_variant, home_team, away_team, features.get("situational", {}),
            ),
            "market": self.call(
                "market", templates["market"], game_id, context_hash, model, prompt_variant,
                home_team, away_team, features.get("market", {}),
            ),
        }


@register_strategy("specialist_synthesis")
class SpecialistSynthesisStrategy:
    name = "specialist_synthesis"
    required_roles = ("stats", "situational", "market", "synthesis")

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

            synthesis_prompt = templates["synthesis"].format(
                home_team=home_team,
                away_team=away_team,
                stats_json=json.dumps(_prediction_summary(specialists["stats"]), sort_keys=True),
                situational_json=json.dumps(
                    _prediction_summary(specialists["situational"]), sort_keys=True
                ),
                market_json=json.dumps(_prediction_summary(specialists["market"]), sort_keys=True),
            )
            predictions[game_id] = llm.predict(
                self.conn,
                model=config["model"],
                agent_role="synthesis",
                game_id=game_id,
                context_hash=game_context["context_hash"],
                agent_design=self.name,
                prompt_variant=prompts["id"],
                prompt_text=synthesis_prompt,
            )

        return predictions
