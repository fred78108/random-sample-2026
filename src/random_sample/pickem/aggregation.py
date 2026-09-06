"""Cross-season aggregation (DESIGN §9.3): rolls `season_summary` rows up across every
tested season into one row per config, answering "which config should be promoted" rather
than just "which config won this one season." No season alone is trusted for that decision
(§9.3: "any single season is a small, noisy sample")."""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass

from random_sample.pickem.db import repository

NAIVE_BASELINE_DESIGN = "naive_favorite"
MARKET_BASELINE_DESIGN = "market_favorite"


@dataclass
class ConfigAggregate:
    agent_design: str
    prompt_variant: str
    model: str
    context_level: str
    seasons_played: int
    mean_season_points: float
    stdev_season_points: float
    seasons_won: int
    margin_vs_naive: float | None
    margin_vs_market: float | None

    @property
    def label(self) -> str:
        return f"{self.agent_design}/{self.prompt_variant}/{self.model}/{self.context_level}"

    @property
    def is_baseline(self) -> bool:
        return self.agent_design in (NAIVE_BASELINE_DESIGN, MARKET_BASELINE_DESIGN)


def _config_key(row: dict) -> tuple[str, str, str, str]:
    return (row["agent_design"], row["prompt_variant"], row["model"], row["context_level"])


def _mean_points_per_season(rows: list[dict]) -> dict[int, float]:
    """One season may hold more than one row for the same `agent_design` if it was swept
    at several prompt_variant/model/context_level combinations (e.g. `market_favorite` run
    at both `standard` and `rich`) -- average those before using the design as a baseline,
    so a wider baseline sweep doesn't silently outweigh a narrower one."""
    by_season: dict[int, list[int]] = {}
    for row in rows:
        by_season.setdefault(row["season"], []).append(row["season_points"])
    return {season: statistics.mean(points) for season, points in by_season.items()}


def aggregate_configs(conn: sqlite3.Connection, seasons: list[int]) -> list[ConfigAggregate]:
    """One `ConfigAggregate` per distinct (agent_design, prompt_variant, model,
    context_level) tested across `seasons`, including the `naive_favorite`/
    `market_favorite` baseline rows themselves (their own aggregate has `margin_vs_*
    == 0`, since a baseline's margin against itself is meaningless but still well-defined)."""
    rows = repository.season_summary_rows(conn, seasons)
    if not rows:
        return []

    naive_by_season = _mean_points_per_season(
        [r for r in rows if r["agent_design"] == NAIVE_BASELINE_DESIGN]
    )
    market_by_season = _mean_points_per_season(
        [r for r in rows if r["agent_design"] == MARKET_BASELINE_DESIGN]
    )

    by_config: dict[tuple[str, str, str, str], list[dict]] = {}
    for row in rows:
        by_config.setdefault(_config_key(row), []).append(row)

    aggregates = []
    for key, config_rows in by_config.items():
        agent_design, prompt_variant, model, context_level = key
        season_points = [r["season_points"] for r in config_rows]
        seasons_won = sum(1 for r in config_rows if r["season_rank"] == 1)

        def _margin(baseline_by_season: dict[int, float]) -> float | None:
            paired = [
                (r["season_points"], baseline_by_season[r["season"]])
                for r in config_rows
                if r["season"] in baseline_by_season
            ]
            if not paired:
                return None
            return statistics.mean(points - baseline for points, baseline in paired)

        aggregates.append(
            ConfigAggregate(
                agent_design=agent_design,
                prompt_variant=prompt_variant,
                model=model,
                context_level=context_level,
                seasons_played=len(config_rows),
                mean_season_points=statistics.mean(season_points),
                stdev_season_points=(
                    statistics.stdev(season_points) if len(season_points) > 1 else 0.0
                ),
                seasons_won=seasons_won,
                margin_vs_naive=_margin(naive_by_season),
                margin_vs_market=_margin(market_by_season),
            )
        )

    aggregates.sort(key=lambda a: a.mean_season_points, reverse=True)
    return aggregates
