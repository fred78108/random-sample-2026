"""Milestone E: smoke test for the self-contained backtest report.html generator (DESIGN
§9.4, §10 decision #6) -- runs the real backtest harness (LLM faked out, same pattern as
test_harness.py) across a tiny two-config grid, then confirms `generate_backtest_report`
produces one HTML file embedding the leaderboard and every chart, with no linked assets."""

from __future__ import annotations

import json

import pytest

from random_sample.pickem import llm, prompts, strategies
from random_sample.pickem.backtest import harness
from random_sample.pickem.db import repository
from random_sample.pickem.viz import report as report_viz

SEASON = 2025


@pytest.fixture
def conn(tmp_path):
    prompts.load_all()
    strategies.load_all()
    connection = repository.get_connection(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def fake_predict(monkeypatch):
    team_by_game: dict[str, tuple[str, str]] = {}

    def fake(conn, *, model, agent_role, game_id, context_hash, agent_design, prompt_variant, prompt_text):
        home, _away = team_by_game[game_id]
        key = llm.cache_key(game_id, context_hash, agent_design, prompt_variant, model, agent_role)
        cached = repository.get_cached_response(conn, key)
        if cached is not None:
            data = json.loads(cached)
        else:
            data = {"predicted_winner": home, "win_probability": 0.65, "rationale": "fake"}
            repository.set_cached_response(conn, key, game_id, json.dumps(data, sort_keys=True))
        return {
            "game_id": game_id, "agent_role": agent_role,
            "predicted_winner": data["predicted_winner"],
            "win_probability": data["win_probability"], "rationale": data["rationale"],
        }

    monkeypatch.setattr(llm, "predict", fake)
    return team_by_game


def _insert_final_game(conn, game_id, week, home, away, home_score, away_score):
    repository.upsert_games(
        conn,
        [
            {
                "game_id": game_id, "season": SEASON, "week": week,
                "home_team": home, "away_team": away,
                "kickoff_ts": f"2025-09-{6 + week:02d}T13:00:00-04:00",
                "home_score": home_score, "away_score": away_score,
            }
        ],
    )
    repository.set_week_synced_final(conn, SEASON, week, True)


def test_generate_backtest_report_bundles_leaderboard_and_charts(conn, fake_predict, tmp_path):
    for game_id, week, home, away, hs, as_ in [
        ("g1", 1, "KC", "BUF", 27, 20),
        ("g2", 2, "KC", "BUF", 20, 23),
    ]:
        _insert_final_game(conn, game_id, week, home, away, hs, as_)
        fake_predict[game_id] = (home, away)

    grid = harness.build_grid(
        agent_designs=["single_analyst", "naive_favorite"],
        prompt_variants=["single_analyst_v1"],
        models=["fake-model"],
        context_levels=["minimal"],
    )
    # naive_favorite's required_roles == () makes it compatible with any prompt_variant
    # (DESIGN §9.3), so both cells run -- one config each, no incompatible skips here.
    summary = harness.run_backtest(conn, seasons=[SEASON], grid=grid)
    assert summary.runs_completed == 4  # 2 configs x 2 weeks
    assert not summary.incompatible_configs

    out_path = report_viz.generate_backtest_report(
        conn, seasons=[SEASON], batch_id="test-batch", output_root=tmp_path
    )

    assert out_path == tmp_path / "test-batch" / "report.html"
    html = out_path.read_text()
    assert "<table class=\"leaderboard\">" in html
    assert "single_analyst" in html
    assert "naive_favorite" in html
    assert html.count("<svg") == 3  # season trend, calibration, baseline comparison
    assert "src=" not in html  # everything embedded, no linked assets (DESIGN §10 #6)


def test_generate_backtest_report_handles_no_data(conn, tmp_path):
    out_path = report_viz.generate_backtest_report(
        conn, seasons=[2099], batch_id="empty-batch", output_root=tmp_path
    )
    assert "No backtest runs found" in out_path.read_text()
