"""Grid-cell idempotency for the backtest harness. See specs/pickem-agent/DESIGN.md §4, §9.1.

The LLM-response cache keyed on (game, context_hash, agent_design, prompt_variant, model,
agent_role) already exists — it's `llm.py`'s `cache_key`/`predict`, built in Milestone A and
already shared verbatim between `recommend` and `backtest` runs (DESIGN §4: "llm_cache is
shared across live and backtest"). Backtest gets that for free because decision #1 has it
invoke the exact same graph, which calls the exact same strategies, which call `llm.predict`
directly — nothing new is needed there.

What backtest does need that live doesn't: a sweep is many (season, week, config) grid cells
and is expected to be interrupted and re-run rather than always finishing in one sitting
(DESIGN §3.3's "full cross-product is likely impractical at first" implies partial/targeted
runs are the norm). `insert_run`/`insert_picks` are plain INSERTs with no natural "already did
this" check, so re-running a sweep would silently duplicate `runs` rows for a grid cell
already completed and double-count it in `season_summary`. `already_run` is that check, one
level up from the LLM cache: not "was this exact prompt already answered" but "was this exact
grid cell already played and scored".
"""

from __future__ import annotations

import sqlite3

from random_sample.pickem.db import repository
from random_sample.pickem.state import RunConfig


def already_run(conn: sqlite3.Connection, season: int, week: int, config: RunConfig) -> bool:
    return (
        repository.get_backtest_run(
            conn,
            season,
            week,
            config["agent_design"],
            config["prompt_variant"],
            config["model"],
            config["context_level"],
        )
        is not None
    )
