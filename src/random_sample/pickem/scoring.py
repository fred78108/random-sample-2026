"""Result ingestion: grade a live week's picks against real outcomes. See
specs/pickem-agent/DESIGN.md §5.2. Deliberately bypasses the LangGraph pipeline and talks to
`nflverse_client`/`db.repository` directly — there's nothing to predict here, only results to
grade."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from random_sample.pickem import nflverse_client
from random_sample.pickem.db import repository


class NoLiveRunError(Exception):
    """Raised when `score` is asked to grade a (season, week) with no `recommend` run on file."""


@dataclass
class ScoreSummary:
    season: int
    week: int
    final_count: int
    pending_count: int
    week_points: int
    season_points: int


def _winner(game: dict) -> str | None:
    """The winning team, or None if the game isn't final or ended in a tie (ties can't be
    "correctly" picked, so they're graded as final-but-incorrect below)."""
    if game["home_score"] is None or game["away_score"] is None:
        return None
    if game["home_score"] == game["away_score"]:
        return None
    return game["home_team"] if game["home_score"] > game["away_score"] else game["away_team"]


@dataclass
class GradedPick:
    game_id: str
    correct: bool
    points_earned: int


def grade_picks(
    games_by_id: dict[str, dict], picks: list[dict]
) -> tuple[list[GradedPick], int, int]:
    """Grade a set of picks against known results — the common step shared by live `score`
    and the backtest harness (DESIGN §9.1: backtest scores against already-final historical
    labels the same way `score` grades live results). A game missing a final score is skipped
    (pending), never scored as incorrect. Returns (graded, pending_count, week_points)."""
    graded = []
    pending_count = 0
    week_points = 0
    for pick in picks:
        game = games_by_id[pick["game_id"]]
        if game["home_score"] is None or game["away_score"] is None:
            pending_count += 1
            continue
        correct = _winner(game) == pick["predicted_winner"]
        points_earned = pick["confidence"] if correct else 0
        graded.append(GradedPick(pick["game_id"], correct, points_earned))
        week_points += points_earned
    return graded, pending_count, week_points


def score_week(conn: sqlite3.Connection, season: int, week: int) -> ScoreSummary:
    """Grade every final game in this week's live run's picks; idempotent — safe to re-run
    as more games go final over the course of a Sunday."""
    nflverse_client.ensure_week(conn, season, week)

    run = repository.get_live_run(conn, season, week)
    if run is None:
        raise NoLiveRunError(
            f"no `recommend` run found for season {season} week {week} — run `pickem recommend` first"
        )
    picks = repository.get_picks_for_run(conn, run["run_id"])
    games_by_id = {g["game_id"]: g for g in repository.games_with_results(conn, season, week)}

    graded, pending_count, week_points = grade_picks(games_by_id, picks)
    for row in graded:
        repository.upsert_score(conn, run["run_id"], row.game_id, row.correct, row.points_earned)

    return ScoreSummary(
        season=season,
        week=week,
        final_count=len(graded),
        pending_count=pending_count,
        week_points=week_points,
        season_points=repository.live_season_points(conn, season),
    )
