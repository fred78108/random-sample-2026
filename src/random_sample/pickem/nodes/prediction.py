"""Prediction Agent node: registry dispatch to a registered PredictionStrategy."""

from __future__ import annotations

import sqlite3
from typing import Callable

from random_sample.pickem.prompts import PROMPT_SET_REGISTRY
from random_sample.pickem.state import PickemState
from random_sample.pickem.strategies import STRATEGY_REGISTRY


def build_prediction_node(conn: sqlite3.Connection) -> Callable[[PickemState], dict]:
    def prediction_node(state: PickemState) -> dict:
        config = state["config"]
        agent_design = config["agent_design"]
        prompt_variant = config["prompt_variant"]

        strategy_cls = STRATEGY_REGISTRY.get(agent_design)
        if strategy_cls is None:
            raise ValueError(f"Unknown agent_design {agent_design!r}")

        prompt_set = PROMPT_SET_REGISTRY.get(prompt_variant)
        if prompt_set is None:
            raise ValueError(f"Unknown prompt_variant {prompt_variant!r}")

        missing_roles = set(strategy_cls.required_roles) - set(prompt_set["templates"])
        if missing_roles:
            raise ValueError(
                f"prompt_variant {prompt_variant!r} is missing roles {sorted(missing_roles)} "
                f"required by agent_design {agent_design!r}"
            )

        strategy = strategy_cls(conn)
        predictions = strategy.predict(state["games"], state["context"], config, prompt_set)
        raw_predictions = {game_id: [prediction] for game_id, prediction in predictions.items()}
        return {"predictions": predictions, "raw_predictions": raw_predictions}

    return prediction_node
