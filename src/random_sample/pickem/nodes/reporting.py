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


def _print_table(state: PickemState) -> None:
    games_by_id = {g["game_id"]: g for g in state["games"]}
    console = Console()
    table = Table(title=f"Week {state['week']}, {state['season']} — {state['config']['agent_design']}")
    table.add_column("Conf", justify="right")
    table.add_column("Matchup")
    table.add_column("Pick")
    table.add_column("Win Prob", justify="right")

    for pick in sorted(state["picks"], key=lambda p: p["confidence"], reverse=True):
        game = games_by_id[pick["game_id"]]
        matchup = f"{game['away_team']} @ {game['home_team']}"
        table.add_row(
            str(pick["confidence"]),
            matchup,
            pick["predicted_winner"],
            f"{pick['win_probability']:.0%}",
        )
    console.print(table)

    if state["validation_errors"]:
        console.print(
            f"[bold red]Validation failed after {state['retries']} retries:[/bold red] "
            + "; ".join(state["validation_errors"])
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
