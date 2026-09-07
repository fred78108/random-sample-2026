"""Milestone D smoke test: a tiny grid runs end to end through the real harness/graph
wiring. `llm.predict` is faked out (same spirit as test_scoring.py stubbing
nflverse_client) so the test never talks to a real Ollama server, but every other layer —
graph, strategies, prompts, context, ranking, validation, persistence, scoring, grid-cell
idempotency — is exercised for real."""

from __future__ import annotations

import json
import logging

import pytest

from random_sample.pickem import llm, prompts, strategies
from random_sample.pickem.backtest import harness
from random_sample.pickem.db import repository

SEASON = 2025


@pytest.fixture
def conn(tmp_path):
    prompts.load_all()
    strategies.load_all()
    connection = repository.get_connection(tmp_path / "test.db")
    yield connection
    connection.close()


def _insert_final_game(conn, game_id, week, home, away, home_score, away_score):
    repository.upsert_games(
        conn,
        [
            {
                "game_id": game_id,
                "season": SEASON,
                "week": week,
                "home_team": home,
                "away_team": away,
                "kickoff_ts": f"2025-09-{6 + week:02d}T13:00:00-04:00",
                "home_score": home_score,
                "away_score": away_score,
            }
        ],
    )
    repository.set_week_synced_final(conn, SEASON, week, True)


@pytest.fixture
def fake_predict(monkeypatch):
    """Stands in for `llm.predict` — always "predicts" the home team, but otherwise
    reproduces predict()'s real llm_cache read-through so cache-sharing/idempotency
    behavior under test stays representative of the real thing."""
    team_by_game: dict[str, tuple[str, str]] = {}

    def fake(
        conn, *, model, agent_role, game_id, context_hash, agent_design, prompt_variant, prompt_text,
        response_schema=None,
    ):
        home, _away = team_by_game[game_id]
        key = llm.cache_key(game_id, context_hash, agent_design, prompt_variant, model, agent_role)
        cached = repository.get_cached_response(conn, key)
        if cached is not None:
            data = json.loads(cached)
        else:
            data = {"predicted_winner": home, "win_probability": 0.65, "rationale": "fake"}
            repository.set_cached_response(conn, key, game_id, json.dumps(data, sort_keys=True))
        return {
            "game_id": game_id,
            "agent_role": agent_role,
            "predicted_winner": data["predicted_winner"],
            "win_probability": data["win_probability"],
            "rationale": data["rationale"],
        }

    monkeypatch.setattr(llm, "predict", fake)
    return team_by_game


_GAMES = [
    ("g1", 1, "KC", "BUF", 27, 20),
    ("g2", 1, "SF", "DAL", 24, 17),
    ("g3", 2, "KC", "DAL", 20, 23),
    ("g4", 2, "SF", "BUF", 30, 10),
]


def _seed(conn, fake_predict):
    for game_id, week, home, away, home_score, away_score in _GAMES:
        _insert_final_game(conn, game_id, week, home, away, home_score, away_score)
        fake_predict[game_id] = (home, away)


def test_tiny_grid_runs_end_to_end(conn, fake_predict, caplog):
    _seed(conn, fake_predict)

    # 2 agent_designs x 2 prompt_variants x 1 model x 1 context_level = 4 cells, but only
    # the "matching" pairing is role-compatible for each design (DESIGN §3.3, §10 decision
    # #5) -- so 2 of the 4 cells are expected to be skipped-with-warning, not run.
    grid = harness.build_grid(
        agent_designs=["single_analyst", "specialist_deterministic"],
        prompt_variants=["single_analyst_v1", "specialist_v1"],
        models=["fake-model"],
        context_levels=["minimal"],
    )

    with caplog.at_level(logging.WARNING):
        summary = harness.run_backtest(conn, seasons=[SEASON], grid=grid)

    assert len(summary.incompatible_configs) == 2
    assert "skipping incompatible grid cell" in caplog.text

    # 2 compatible configs x 2 weeks = 4 completed grid cells.
    assert summary.runs_completed == 4
    assert summary.runs_skipped_already_done == 0

    run_count = conn.execute(
        "SELECT COUNT(*) AS n FROM runs WHERE run_type = 'backtest'"
    ).fetchone()["n"]
    assert run_count == 4

    # 2 games/week x 2 weeks x 2 configs = 8 scored picks.
    score_count = conn.execute("SELECT COUNT(*) AS n FROM scores").fetchone()["n"]
    assert score_count == 8

    # Re-running the identical sweep must not duplicate rows (backtest/cache.py's
    # grid-cell idempotency) -- every cell is now "already run" instead.
    summary2 = harness.run_backtest(conn, seasons=[SEASON], grid=grid)
    assert summary2.runs_completed == 0
    assert summary2.runs_skipped_already_done == 4

    run_count_after = conn.execute(
        "SELECT COUNT(*) AS n FROM runs WHERE run_type = 'backtest'"
    ).fetchone()["n"]
    assert run_count_after == 4
    score_count_after = conn.execute("SELECT COUNT(*) AS n FROM scores").fetchone()["n"]
    assert score_count_after == 8


def test_season_summary_view_ranks_backtest_configs(conn, fake_predict):
    _seed(conn, fake_predict)
    grid = harness.build_grid(
        agent_designs=["single_analyst"],
        prompt_variants=["single_analyst_v1"],
        models=["fake-model"],
        context_levels=["minimal"],
    )
    harness.run_backtest(conn, seasons=[SEASON], grid=grid)

    rows = conn.execute(
        "SELECT * FROM season_summary WHERE season = ? AND agent_design = 'single_analyst'",
        (SEASON,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["season_rank"] == 1


def test_failed_cell_is_skipped_and_recoverable_on_rerun(conn, fake_predict, caplog, monkeypatch):
    """Milestone F: a cell that keeps failing (e.g. throttling exhausted after llm.py's own
    retries) must not take down the whole sweep, and a later re-invocation with the same
    grid must pick that cell back up rather than being stuck skipping it forever."""
    _seed(conn, fake_predict)
    grid = harness.build_grid(
        agent_designs=["single_analyst"],
        prompt_variants=["single_analyst_v1"],
        models=["fake-model"],
        context_levels=["minimal"],
    )

    working_predict = llm.predict  # `fake_predict` already installed this
    attempts = {"count": 0}

    def flaky_predict(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("simulated: throttling retry budget exhausted")
        return working_predict(*args, **kwargs)

    monkeypatch.setattr(llm, "predict", flaky_predict)

    with caplog.at_level(logging.ERROR):
        summary = harness.run_backtest(conn, seasons=[SEASON], grid=grid)

    # 1 game_id's first call fails -> the whole cell for that week is skipped, not the
    # other week's cell.
    assert summary.runs_failed == 1
    assert summary.runs_completed == 1
    assert "grid cell failed, skipping" in caplog.text
    run_count = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
    assert run_count == 1

    # Re-running the identical command with the same grid must not treat the failed week
    # as permanently done -- it retries that cell and this time succeeds, while the
    # already-completed week is skipped rather than redone.
    summary2 = harness.run_backtest(conn, seasons=[SEASON], grid=grid)
    assert summary2.runs_completed == 1
    assert summary2.runs_skipped_already_done == 1
    assert summary2.runs_failed == 0
    run_count_after = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
    assert run_count_after == 2
