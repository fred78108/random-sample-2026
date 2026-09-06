"""Milestone E: cross-season aggregation (DESIGN §9.3) -- mean/stdev season points,
seasons won, and margin vs. the naive/market baselines, over a hand-built two-season
fixture with known totals."""

from __future__ import annotations

import pytest

from random_sample.pickem import aggregation
from random_sample.pickem.db import repository

REAL_DESIGN = "single_analyst"


@pytest.fixture
def conn(tmp_path):
    connection = repository.get_connection(tmp_path / "test.db")
    yield connection
    connection.close()


def _seed(conn, run_id: str, season: int, agent_design: str, points: int, **config) -> None:
    game_id = f"{run_id}_g"
    repository.upsert_games(
        conn,
        [
            {
                "game_id": game_id, "season": season, "week": 1,
                "home_team": "KC", "away_team": "BUF",
                "kickoff_ts": f"{season}-09-07T13:00:00-04:00",
            }
        ],
    )
    repository.insert_run(
        conn, run_id=run_id, run_type="backtest", season=season, week=1,
        agent_design=agent_design,
        prompt_variant=config.get("prompt_variant", "p"),
        model=config.get("model", "m"),
        context_level=config.get("context_level", "minimal"),
    )
    repository.upsert_score(conn, run_id, game_id, correct=True, points_earned=points)


def test_aggregate_configs_computes_mean_stdev_and_seasons_won(conn):
    # real config: 10 in 2024, 20 in 2025 -> mean 15, wins 2025 (naive scores lower there).
    _seed(conn, "real_2024", 2024, REAL_DESIGN, points=10)
    _seed(conn, "real_2025", 2025, REAL_DESIGN, points=20)
    _seed(conn, "naive_2024", 2024, "naive_favorite", points=12)
    _seed(conn, "naive_2025", 2025, "naive_favorite", points=14)

    aggregates = aggregation.aggregate_configs(conn, seasons=[2024, 2025])
    real = next(a for a in aggregates if a.agent_design == REAL_DESIGN)

    assert real.seasons_played == 2
    assert real.mean_season_points == 15.0
    assert real.stdev_season_points == pytest.approx(7.0710678, rel=1e-4)
    assert real.seasons_won == 1  # only beat naive_favorite outright in 2025
    assert real.margin_vs_naive == pytest.approx((10 - 12 + 20 - 14) / 2)
    assert real.margin_vs_market is None  # no market_favorite rows seeded at all


def test_aggregate_configs_averages_multiple_baseline_rows_per_season(conn):
    # market_favorite swept at two context levels in the same season -- its per-season
    # baseline value must be the average of those rows, not double-counted or arbitrary.
    _seed(conn, "real_1", 2025, REAL_DESIGN, points=20)
    _seed(conn, "market_standard", 2025, "market_favorite", points=10, context_level="standard")
    _seed(conn, "market_rich", 2025, "market_favorite", points=14, context_level="rich")

    aggregates = aggregation.aggregate_configs(conn, seasons=[2025])
    real = next(a for a in aggregates if a.agent_design == REAL_DESIGN)

    assert real.margin_vs_market == pytest.approx(20 - 12)  # 12 = mean(10, 14)


def test_aggregate_configs_sorted_by_mean_points_descending(conn):
    _seed(conn, "low", 2025, "design_low", points=5)
    _seed(conn, "high", 2025, "design_high", points=25)

    aggregates = aggregation.aggregate_configs(conn, seasons=[2025])
    assert [a.agent_design for a in aggregates] == ["design_high", "design_low"]


def test_aggregate_configs_empty_when_no_backtest_runs(conn):
    assert aggregation.aggregate_configs(conn, seasons=[2025]) == []
