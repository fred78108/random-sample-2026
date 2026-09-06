"""Walk-forward backtest harness. See specs/pickem-agent/DESIGN.md §9.1.

Reuses the exact live graph unchanged (decision #1) — a grid cell differs from a live
`recommend` run only in `RunConfig` and `run_type`. For each requested season, replays
weeks 1-18 in order; for each week, every grid config is run once against that week's
already-final historical slate (point-in-time correctness is `nodes/context.py`'s job —
see `tests/pickem/test_point_in_time.py` — the harness's own responsibility is *which*
weeks/configs get run and in what order).
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from itertools import product

from random_sample.pickem import nflverse_client, scoring
from random_sample.pickem.backtest import cache as backtest_cache
from random_sample.pickem.db import repository
from random_sample.pickem.graph import build_graph
from random_sample.pickem.prompts import PROMPT_SET_REGISTRY
from random_sample.pickem.state import RunConfig
from random_sample.pickem.strategies import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

LAST_REGULAR_SEASON_WEEK = 18


@dataclass
class BacktestSummary:
    seasons: list[int]
    incompatible_configs: list[RunConfig] = field(default_factory=list)
    runs_completed: int = 0
    runs_skipped_already_done: int = 0
    weeks_skipped_not_final: int = 0
    runs_failed: int = 0


def parse_seasons(spec: str) -> list[int]:
    """"2022-2024,2026" -> [2022, 2023, 2024, 2026] (DESIGN §5's `--seasons` example)."""
    seasons: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-")
            seasons.update(range(int(start), int(end) + 1))
        else:
            seasons.add(int(part))
    return sorted(seasons)


def parse_list(spec: str) -> list[str]:
    """"a,b,c" -> ["a", "b", "c"] — used for the other comma-separated `backtest` flags."""
    return [part.strip() for part in spec.split(",") if part.strip()]


def build_grid(
    agent_designs: list[str],
    prompt_variants: list[str],
    models: list[str],
    context_levels: list[str],
) -> list[RunConfig]:
    return [
        {
            "agent_design": agent_design,
            "prompt_variant": prompt_variant,
            "model": model,
            "context_level": context_level,
        }
        for agent_design, prompt_variant, model, context_level in product(
            agent_designs, prompt_variants, models, context_levels
        )
    ]


def _missing_roles(config: RunConfig) -> set[str] | None:
    """None if either registry key is unknown (nothing sensible to report); otherwise the
    set of required roles the prompt variant doesn't cover (empty set = compatible)."""
    strategy_cls = STRATEGY_REGISTRY.get(config["agent_design"])
    prompt_set = PROMPT_SET_REGISTRY.get(config["prompt_variant"])
    if strategy_cls is None or prompt_set is None:
        return None
    return set(strategy_cls.required_roles) - set(prompt_set["templates"])


def _partition_grid(grid: list[RunConfig]) -> tuple[list[RunConfig], list[RunConfig]]:
    """Split the grid into (compatible, incompatible) up front (DESIGN §3.3, §10 decision
    #5: an incompatible agent-design/prompt-variant pair is skip-with-warning for a
    backtest sweep, never the hard error `recommend` raises) — so an incompatible cell is
    never even submitted to the graph, rather than caught mid-run."""
    compatible, incompatible = [], []
    for config in grid:
        missing = _missing_roles(config)
        if missing:
            logger.warning(
                "skipping incompatible grid cell: agent_design=%r prompt_variant=%r "
                "missing roles %s",
                config["agent_design"], config["prompt_variant"], sorted(missing),
            )
            incompatible.append(config)
        elif missing is None:
            logger.warning(
                "skipping unknown grid cell: agent_design=%r prompt_variant=%r",
                config["agent_design"], config["prompt_variant"],
            )
            incompatible.append(config)
        else:
            compatible.append(config)
    return compatible, incompatible


def _initial_state(run_id: str, season: int, week: int, config: RunConfig) -> dict:
    return {
        "run_id": run_id,
        "run_type": "backtest",
        "season": season,
        "week": week,
        "config": config,
        "games": [],
        "context": {},
        "raw_predictions": {},
        "predictions": {},
        "picks": [],
        "validation_errors": [],
        "retries": 0,
    }


def run_backtest(
    conn: sqlite3.Connection, seasons: list[int], grid: list[RunConfig]
) -> BacktestSummary:
    compatible_grid, incompatible_grid = _partition_grid(grid)
    summary = BacktestSummary(seasons=seasons, incompatible_configs=incompatible_grid)
    if not compatible_grid:
        return summary

    graph = build_graph(conn)
    for season in seasons:
        nflverse_client.ensure_season(conn, season)
        for week in range(1, LAST_REGULAR_SEASON_WEEK + 1):
            if not repository.week_sync_is_final(conn, season, week):
                # Not yet played (future week) or in progress — a walk-forward replay can
                # only score against fully-known historical labels (DESIGN §9.1).
                summary.weeks_skipped_not_final += len(compatible_grid)
                continue
            games = repository.games_from_db(conn, season, week)
            if not games:
                continue

            results = repository.games_with_results(conn, season, week)
            games_by_id = {g["game_id"]: g for g in results}
            for config in compatible_grid:
                if backtest_cache.already_run(conn, season, week, config):
                    summary.runs_skipped_already_done += 1
                    continue

                run_id = str(uuid.uuid4())
                try:
                    final_state = graph.invoke(
                        _initial_state(run_id, season, week, config), config={"recursion_limit": 50}
                    )
                except Exception:
                    # A cell that still fails after llm.py's own retry/backoff (throttling
                    # exhausted, or some other transient failure) shouldn't take down an
                    # hours-long sweep. `already_run` never saw this cell complete, so a later
                    # `pickem backtest` re-invocation with the same flags will retry it —
                    # every already-cached LLM call and already-completed cell is unaffected.
                    logger.exception(
                        "grid cell failed, skipping: season=%d week=%d config=%r",
                        season, week, config,
                    )
                    summary.runs_failed += 1
                    continue

                graded, _pending_count, _week_points = scoring.grade_picks(
                    games_by_id, final_state["picks"]
                )
                for row in graded:
                    repository.upsert_score(
                        conn, run_id, row.game_id, row.correct, row.points_earned
                    )
                summary.runs_completed += 1

    return summary
