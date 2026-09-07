"""Reporting Agent: CLI table + runs/picks persistence + live weekly charts (DESIGN §8)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table

from random_sample.pickem.db import repository
from random_sample.pickem.state import PickemState
from random_sample.pickem.viz import charts

REPORTS_ROOT = Path("reports")


def _team_score_extremes(state: PickemState) -> tuple[dict | None, dict | None]:
    """Every team's predicted score across the week's whole slate (home and away sides of
    every game that has one, PRD FR9) -- returns the single highest- and single
    lowest-predicted-score team as `{"team": ..., "score": ...}`, or `(None, None)` if no
    pick in this run carries predicted scores (every non-promoted strategy). Tie-break:
    earliest kickoff first, then team abbreviation, for a fully deterministic result."""
    games_by_id = {g["game_id"]: g for g in state["games"]}
    entries = []
    for pick in state["picks"]:
        if "predicted_home_score" not in pick or "predicted_away_score" not in pick:
            continue
        game = games_by_id[pick["game_id"]]
        entries.append((game["home_team"], pick["predicted_home_score"], game["kickoff_ts"]))
        entries.append((game["away_team"], pick["predicted_away_score"], game["kickoff_ts"]))

    if not entries:
        return None, None

    highest_team, highest_score, _ = min(entries, key=lambda e: (-e[1], e[2], e[0]))
    lowest_team, lowest_score, _ = min(entries, key=lambda e: (e[1], e[2], e[0]))
    return (
        {"team": highest_team, "score": highest_score},
        {"team": lowest_team, "score": lowest_score},
    )


def _print_table(state: PickemState) -> None:
    games_by_id = {g["game_id"]: g for g in state["games"]}
    console = Console()
    table = Table(title=f"Week {state['week']}, {state['season']} — {state['config']['agent_design']}")
    table.add_column("Conf", justify="right")
    table.add_column("Matchup")
    table.add_column("Pick")
    table.add_column("Win Prob", justify="right")
    table.add_column("Home Score", justify="right")
    table.add_column("Away Score", justify="right")

    for pick in sorted(state["picks"], key=lambda p: p["confidence"], reverse=True):
        game = games_by_id[pick["game_id"]]
        matchup = f"{game['away_team']} @ {game['home_team']}"
        table.add_row(
            str(pick["confidence"]),
            matchup,
            pick["predicted_winner"],
            f"{pick['win_probability']:.0%}",
            f"{pick['predicted_home_score']:.1f}" if "predicted_home_score" in pick else "—",
            f"{pick['predicted_away_score']:.1f}" if "predicted_away_score" in pick else "—",
        )
    console.print(table)

    if state["validation_errors"]:
        console.print(
            f"[bold red]Validation failed after {state['retries']} retries:[/bold red] "
            + "; ".join(state["validation_errors"])
        )

    # PRD FR9 / Milestone G: display-only weekly extremes, only meaningful for a live run's
    # single promoted config -- a backtest sweep isn't a coherent single "week's slate" to
    # summarize this way, and its non-promoted grid cells mostly have no scores at all.
    if state["run_type"] == "live":
        highest, lowest = _team_score_extremes(state)
        if highest is not None and lowest is not None:
            console.print(
                f"Highest predicted score: [bold]{highest['team']}[/bold] ({highest['score']:.1f})"
                f" — Lowest: [bold]{lowest['team']}[/bold] ({lowest['score']:.1f})"
            )


def build_reporting_node(conn: sqlite3.Connection) -> Callable[[PickemState], dict]:
    def reporting_node(state: PickemState) -> dict:
        status = "failed_validation" if state["validation_errors"] else "ok"
        config = state["config"]
        repository.insert_run(
            conn,
            run_id=state["run_id"],
            run_type=state["run_type"],
            season=state["season"],
            week=state["week"],
            agent_design=config["agent_design"],
            prompt_variant=config["prompt_variant"],
            model=config["model"],
            context_level=config["context_level"],
            status=status,
        )
        repository.insert_picks(conn, state["run_id"], state["picks"])
        _print_table(state)

        if state["run_type"] == "live":
            week_dir = REPORTS_ROOT / str(state["season"]) / str(state["week"])
            charts.save_live_calibration_scatter(state["picks"], week_dir / "calibration_scatter.svg")
            charts.save_live_season_cumulative(
                conn, state["season"], week_dir / "season_cumulative_points.svg"
            )

        return {}

    return reporting_node
