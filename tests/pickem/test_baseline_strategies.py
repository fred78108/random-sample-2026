"""Milestone E: unit tests for the non-LLM baseline strategies (DESIGN §9.3, PRD §14).
Both strategies are pure functions of `context` -- no DB, no LLM -- so these exercise
`predict()` directly against hand-built feature dicts rather than a full graph run."""

from __future__ import annotations

from random_sample.pickem.strategies.baseline import MarketFavoriteStrategy, NaiveFavoriteStrategy

GAME = {
    "game_id": "g1", "season": 2025, "week": 3,
    "home_team": "KC", "away_team": "BUF",
    "kickoff_ts": "2025-09-21T13:00:00-04:00",
}


def _context(features: dict) -> dict:
    return {"g1": {"game_id": "g1", "features": features, "context_hash": "x"}}


def test_naive_favorite_picks_better_record():
    features = {
        "home_record": {"wins": 3, "losses": 0, "ties": 0},
        "away_record": {"wins": 0, "losses": 3, "ties": 0},
        "home_point_differential": 40,
        "away_point_differential": -40,
    }
    strategy = NaiveFavoriteStrategy(conn=None)
    predictions = strategy.predict([GAME], _context(features), config={}, prompts=None)

    prediction = predictions["g1"]
    assert prediction["predicted_winner"] == "KC"
    assert 0.5 < prediction["win_probability"] <= 1.0


def test_naive_favorite_picks_worse_record_underdog():
    features = {
        "home_record": {"wins": 0, "losses": 3, "ties": 0},
        "away_record": {"wins": 3, "losses": 0, "ties": 0},
        "home_point_differential": -40,
        "away_point_differential": 40,
    }
    strategy = NaiveFavoriteStrategy(conn=None)
    predictions = strategy.predict([GAME], _context(features), config={}, prompts=None)

    prediction = predictions["g1"]
    assert prediction["predicted_winner"] == "BUF"
    assert 0.5 < prediction["win_probability"] <= 1.0


def test_naive_favorite_breaks_even_record_tie_by_point_differential():
    features = {
        "home_record": {"wins": 1, "losses": 1, "ties": 0},
        "away_record": {"wins": 1, "losses": 1, "ties": 0},
        "home_point_differential": -10,
        "away_point_differential": 10,
    }
    strategy = NaiveFavoriteStrategy(conn=None)
    predictions = strategy.predict([GAME], _context(features), config={}, prompts=None)

    prediction = predictions["g1"]
    assert prediction["predicted_winner"] == "BUF"
    assert prediction["win_probability"] == 0.5


def test_naive_favorite_defaults_to_home_team_when_fully_tied():
    features = {
        "home_record": {"wins": 0, "losses": 0, "ties": 0},
        "away_record": {"wins": 0, "losses": 0, "ties": 0},
        "home_point_differential": 0,
        "away_point_differential": 0,
    }
    strategy = NaiveFavoriteStrategy(conn=None)
    predictions = strategy.predict([GAME], _context(features), config={}, prompts=None)

    prediction = predictions["g1"]
    assert prediction["predicted_winner"] == "KC"
    assert prediction["win_probability"] == 0.5


def test_market_favorite_uses_devigged_moneylines():
    features = {"market": {"home_moneyline": -200, "away_moneyline": 170, "spread_line": 4.5}}
    strategy = MarketFavoriteStrategy(conn=None)
    predictions = strategy.predict([GAME], _context(features), config={}, prompts=None)

    prediction = predictions["g1"]
    assert prediction["predicted_winner"] == "KC"
    assert 0.5 < prediction["win_probability"] < 1.0


def test_market_favorite_falls_back_to_spread_when_no_moneylines():
    features = {"market": {"home_moneyline": None, "away_moneyline": None, "spread_line": -6.0}}
    strategy = MarketFavoriteStrategy(conn=None)
    predictions = strategy.predict([GAME], _context(features), config={}, prompts=None)

    prediction = predictions["g1"]
    assert prediction["predicted_winner"] == "BUF"


def test_market_favorite_degrades_to_home_default_without_market_slice():
    # No "market" key at all -- e.g. a market_favorite run at context_level=minimal/standard,
    # which never populates that slice (DESIGN §3.3). A legitimate grid cell, not an error.
    strategy = MarketFavoriteStrategy(conn=None)
    predictions = strategy.predict([GAME], _context({}), config={}, prompts=None)

    prediction = predictions["g1"]
    assert prediction["predicted_winner"] == "KC"
    assert prediction["win_probability"] == 0.5
