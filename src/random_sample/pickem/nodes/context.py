"""Context Agent node. Feature tiers (DESIGN §6): minimal / standard / rich, plus the
situational/market slice builders every specialist strategy role draws from
(Milestone C)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from typing import Callable

import nflreadpy as nfl
import polars as pl

from random_sample.pickem.db import repository
from random_sample.pickem.state import GameContext, PickemState

RECENT_FORM_GAMES = 5
HEAD_TO_HEAD_GAMES = 5


def _team_stats(games: list[dict]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0, "point_differential": 0})
    for game in games:
        home, away = game["home_team"], game["away_team"]
        home_score, away_score = game["home_score"], game["away_score"]
        stats[home]["point_differential"] += home_score - away_score
        stats[away]["point_differential"] += away_score - home_score
        if home_score > away_score:
            stats[home]["wins"] += 1
            stats[away]["losses"] += 1
        elif away_score > home_score:
            stats[away]["wins"] += 1
            stats[home]["losses"] += 1
        else:
            stats[home]["ties"] += 1
            stats[away]["ties"] += 1
    return stats


def _minimal_features(home_team: str, away_team: str, stats: dict[str, dict[str, int]]) -> dict:
    empty = {"wins": 0, "losses": 0, "ties": 0, "point_differential": 0}
    home_stats = stats.get(home_team, empty)
    away_stats = stats.get(away_team, empty)
    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_record": {k: home_stats[k] for k in ("wins", "losses", "ties")},
        "away_record": {k: away_stats[k] for k in ("wins", "losses", "ties")},
        "home_point_differential": home_stats["point_differential"],
        "away_point_differential": away_stats["point_differential"],
    }


def _form_from_games(games: list[dict], team: str) -> dict:
    wins = losses = ties = point_differential = 0
    for game in games:
        if game["home_team"] == team:
            team_score, opp_score = game["home_score"], game["away_score"]
        else:
            team_score, opp_score = game["away_score"], game["home_score"]
        point_differential += team_score - opp_score
        if team_score > opp_score:
            wins += 1
        elif opp_score > team_score:
            losses += 1
        else:
            ties += 1
    return {
        "games_played": len(games),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "point_differential": point_differential,
    }


def _recent_form_features(
    conn: sqlite3.Connection, season: int, week: int, home_team: str, away_team: str
) -> dict:
    home_games = repository.team_recent_games(conn, season, home_team, week, RECENT_FORM_GAMES)
    away_games = repository.team_recent_games(conn, season, away_team, week, RECENT_FORM_GAMES)
    return {
        "home_recent_form": _form_from_games(home_games, home_team),
        "away_recent_form": _form_from_games(away_games, away_team),
    }


_TEAM_STATS_CACHE: dict[int, pl.DataFrame] = {}


def _season_team_stats(season: int) -> pl.DataFrame | None:
    """Cached per-season team_stats frame (nflreadpy caches the underlying fetch itself;
    this just avoids repeating the polars load/filter per game within one context build).
    Best-effort like injuries below: nflverse doesn't publish this file until a season's
    first week has actually been played, so a not-yet-existing season fails soft."""
    if season not in _TEAM_STATS_CACHE:
        try:
            stats = nfl.load_team_stats(seasons=[season])
            _TEAM_STATS_CACHE[season] = stats.filter(pl.col("season_type") == "REG")
        except Exception:
            _TEAM_STATS_CACHE[season] = None
    return _TEAM_STATS_CACHE[season]


def _epa_per_play(team_stats: pl.DataFrame | None, before_week: int, team: str) -> dict:
    if team_stats is None:
        return {"offense": None, "defense_allowed": None}

    offense = team_stats.filter((pl.col("week") < before_week) & (pl.col("team") == team))
    defense = team_stats.filter((pl.col("week") < before_week) & (pl.col("opponent_team") == team))

    def _rate(frame: pl.DataFrame) -> float | None:
        if frame.is_empty():
            return None
        totals = frame.select(
            epa=(pl.col("passing_epa").fill_null(0) + pl.col("rushing_epa").fill_null(0)).sum(),
            plays=(pl.col("attempts").fill_null(0) + pl.col("carries").fill_null(0)).sum(),
        ).row(0, named=True)
        return round(totals["epa"] / totals["plays"], 4) if totals["plays"] else None

    return {"offense": _rate(offense), "defense_allowed": _rate(defense)}


def _epa_features(season: int, week: int, home_team: str, away_team: str) -> dict:
    team_stats = _season_team_stats(season)
    return {
        "home_epa_per_play": _epa_per_play(team_stats, week, home_team),
        "away_epa_per_play": _epa_per_play(team_stats, week, away_team),
    }


def situational_features(fields: dict, home_team: str, away_team: str) -> dict:
    """The `situational` specialist role's isolated slice: rest days, home/away,
    divisional game, surface/roof — no score or opponent-quality data (DESIGN §3.3)."""
    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_rest_days": fields.get("home_rest"),
        "away_rest_days": fields.get("away_rest"),
        "divisional_game": bool(fields.get("div_game")),
        "roof": fields.get("roof"),
        "surface": fields.get("surface"),
    }


def market_features(fields: dict, home_team: str, away_team: str) -> dict:
    """The `market` specialist role's isolated slice: nflverse's own spread/total/
    moneyline fields (DESIGN §3.3) — the same signal the naive market baseline uses."""
    return {
        "home_team": home_team,
        "away_team": away_team,
        "spread_line": fields.get("spread_line"),
        "total_line": fields.get("total_line"),
        "home_moneyline": fields.get("home_moneyline"),
        "away_moneyline": fields.get("away_moneyline"),
    }


def _head_to_head_features(
    conn: sqlite3.Connection, season: int, week: int, home_team: str, away_team: str
) -> dict:
    meetings = repository.head_to_head_games(
        conn, season, week, home_team, away_team, HEAD_TO_HEAD_GAMES
    )
    home_wins = away_wins = ties = 0
    recent = []
    for game in meetings:
        if game["home_score"] == game["away_score"]:
            ties += 1
        elif (game["home_team"] == home_team) == (game["home_score"] > game["away_score"]):
            home_wins += 1
        else:
            away_wins += 1
        recent.append(
            {
                "season": game["season"],
                "week": game["week"],
                "home_team": game["home_team"],
                "away_team": game["away_team"],
                "home_score": game["home_score"],
                "away_score": game["away_score"],
            }
        )
    return {
        "head_to_head": {
            "meetings": recent,
            "home_team_wins": home_wins,
            "away_team_wins": away_wins,
            "ties": ties,
        }
    }


_INJURY_CACHE: dict[int, pl.DataFrame | None] = {}


def _season_injuries(season: int) -> pl.DataFrame | None:
    if season not in _INJURY_CACHE:
        try:
            _INJURY_CACHE[season] = nfl.load_injuries(seasons=[season])
        except Exception:
            _INJURY_CACHE[season] = None
    return _INJURY_CACHE[season]


def _injury_counts(injuries: pl.DataFrame | None, week: int, team: str) -> dict:
    empty = {"out": 0, "doubtful": 0, "questionable": 0, "reports": 0}
    if injuries is None:
        return empty
    team_week = injuries.filter((pl.col("week") == week) & (pl.col("team") == team))
    if team_week.is_empty():
        return empty
    statuses = team_week.get_column("report_status").to_list()
    return {
        "out": sum(1 for s in statuses if s == "Out"),
        "doubtful": sum(1 for s in statuses if s == "Doubtful"),
        "questionable": sum(1 for s in statuses if s == "Questionable"),
        "reports": len(statuses),
    }


def _injury_features(season: int, week: int, home_team: str, away_team: str) -> dict:
    """Best-effort (DESIGN §6): nflverse's injury-report coverage varies by season, and
    an empty report here just means none is available yet for this week."""
    injuries = _season_injuries(season)
    return {
        "home_injuries": _injury_counts(injuries, week, home_team),
        "away_injuries": _injury_counts(injuries, week, away_team),
    }


def _context_hash(features: dict) -> str:
    return hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest()


def build_context_node(conn: sqlite3.Connection) -> Callable[[PickemState], dict]:
    def context_node(state: PickemState) -> dict:
        context_level = state["config"]["context_level"]
        if context_level not in ("minimal", "standard", "rich"):
            raise NotImplementedError(f"context_level={context_level!r} not yet implemented")

        season, week = state["season"], state["week"]
        prior_games = repository.completed_games_before(conn, season, week)
        stats = _team_stats(prior_games)
        situational_market = repository.game_situational_market_fields(conn, season, week)

        context: dict[str, GameContext] = {}
        for game in state["games"]:
            home_team, away_team = game["home_team"], game["away_team"]
            fields = situational_market.get(game["game_id"], {})

            features = _minimal_features(home_team, away_team, stats)
            if context_level in ("standard", "rich"):
                features.update(_recent_form_features(conn, season, week, home_team, away_team))
                features.update(_epa_features(season, week, home_team, away_team))
                features["situational"] = situational_features(fields, home_team, away_team)
            if context_level == "rich":
                features.update(_head_to_head_features(conn, season, week, home_team, away_team))
                features["market"] = market_features(fields, home_team, away_team)
                features.update(_injury_features(season, week, home_team, away_team))

            context[game["game_id"]] = {
                "game_id": game["game_id"],
                "features": features,
                "context_hash": _context_hash(features),
            }
        return {"context": context}

    return context_node
