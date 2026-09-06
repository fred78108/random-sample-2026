"""Typer CLI: `pickem recommend` (Milestone A), `pickem score` (Milestone B), `pickem backtest`
(Milestone D)."""

from __future__ import annotations

import tomllib
import uuid
from datetime import datetime
from pathlib import Path

import typer

from random_sample.pickem import dates, nflverse_client, prompts, scoring, strategies
from random_sample.pickem.backtest import harness
from random_sample.pickem.db import repository
from random_sample.pickem.graph import build_graph
from random_sample.pickem.state import RunConfig
from random_sample.pickem.viz import report as report_viz

app = typer.Typer()

DEFAULT_CONFIG_PATH = Path("config.toml")


@app.callback()
def _main() -> None:
    """Yahoo Pro Pick'em recommendation agent."""


def _load_config(path: Path) -> RunConfig:
    if not path.exists():
        raise typer.BadParameter(f"production config not found at {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)
    return {
        "agent_design": data["agent_design"],
        "prompt_variant": data["prompt_variant"],
        "model": data["model"],
        "context_level": data["context_level"],
    }


def _resolve_week(conn, season: int | None, week: int | None) -> tuple[int, int]:
    if season is not None and week is not None:
        return season, week
    lookup_season = season or datetime.now().year
    schedule = nflverse_client.ensure_season(conn, lookup_season)
    resolved_season, resolved_week = dates.current_week(schedule, datetime.now().astimezone())
    return season or resolved_season, week or resolved_week


def _resolve_score_week(conn, season: int | None, week: int | None) -> tuple[int, int]:
    """Like `_resolve_week`, but defaults to the most recently *completed* week (current
    week minus one) rather than the upcoming one — see DESIGN §5.1."""
    if season is not None and week is not None:
        return season, week
    lookup_season = season or datetime.now().year
    schedule = nflverse_client.ensure_season(conn, lookup_season)
    current_season, current_wk = dates.current_week(schedule, datetime.now().astimezone())
    resolved_season, resolved_week = dates.previous_week(current_season, current_wk)
    return season or resolved_season, week or resolved_week


@app.command()
def recommend(
    week: int = typer.Option(None, "--week", help="NFL week number (1-18)."),
    season: int = typer.Option(None, "--season", help="NFL season year."),
) -> None:
    """Run the graph once with the production config and print/persist the week's picks."""
    prompts.load_all()
    strategies.load_all()

    conn = repository.get_connection()
    config = _load_config(DEFAULT_CONFIG_PATH)
    resolved_season, resolved_week = _resolve_week(conn, season, week)

    graph = build_graph(conn)
    initial_state = {
        "run_id": str(uuid.uuid4()),
        "run_type": "live",
        "season": resolved_season,
        "week": resolved_week,
        "config": config,
        "games": [],
        "context": {},
        "raw_predictions": {},
        "predictions": {},
        "picks": [],
        "validation_errors": [],
        "retries": 0,
    }
    graph.invoke(initial_state, config={"recursion_limit": 50})


@app.command()
def score(
    week: int = typer.Option(None, "--week", help="NFL week number (1-18)."),
    season: int = typer.Option(None, "--season", help="NFL season year."),
) -> None:
    """Grade a week's live picks against real results and print the season-to-date total."""
    conn = repository.get_connection()
    resolved_season, resolved_week = _resolve_score_week(conn, season, week)

    try:
        summary = scoring.score_week(conn, resolved_season, resolved_week)
    except scoring.NoLiveRunError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(
        f"Week {summary.week}, {summary.season}: {summary.final_count}/"
        f"{summary.final_count + summary.pending_count} games final, "
        f"{summary.week_points} points this week"
        + (f", {summary.pending_count} pending" if summary.pending_count else "")
        + f". Season total: {summary.season_points} points."
    )


@app.command()
def backtest(
    seasons: str = typer.Option(
        ..., "--seasons", help="Season(s) to replay, e.g. '2022-2024' or '2022,2024'."
    ),
    agent_design: str = typer.Option(
        ..., "--agent-design", help="Comma-separated agent_design values to sweep."
    ),
    prompt_variant: str = typer.Option(
        ..., "--prompt-variant", help="Comma-separated prompt_variant values to sweep."
    ),
    model: str = typer.Option(
        ..., "--model", help="Comma-separated Ollama model names to sweep."
    ),
    context_level: str = typer.Option(
        ..., "--context-level", help="Comma-separated context levels (minimal,standard,rich)."
    ),
) -> None:
    """Walk-forward replay of a config grid across historical seasons (DESIGN §9.1). Skips
    incompatible agent-design/prompt-variant pairs with a warning rather than aborting."""
    prompts.load_all()
    strategies.load_all()

    conn = repository.get_connection()
    grid = harness.build_grid(
        harness.parse_list(agent_design),
        harness.parse_list(prompt_variant),
        harness.parse_list(model),
        harness.parse_list(context_level),
    )
    summary = harness.run_backtest(conn, harness.parse_seasons(seasons), grid)

    typer.echo(
        f"Backtest: {summary.runs_completed} run(s) completed, "
        f"{summary.runs_skipped_already_done} already done, "
        f"{summary.weeks_skipped_not_final} week(s) not yet final."
    )
    if summary.runs_failed:
        typer.echo(
            f"{summary.runs_failed} grid cell(s) failed even after retrying — re-run the same "
            "`pickem backtest` command to pick up where it left off (already-completed cells "
            "and cached LLM calls are skipped, not redone)."
        )
    if summary.incompatible_configs:
        typer.echo(
            f"Skipped {len(summary.incompatible_configs)} incompatible config(s) "
            "— see warnings above."
        )


@app.command()
def report(
    seasons: str = typer.Option(
        ..., "--seasons", help="Season(s) to include, e.g. '2022-2024' or '2022,2024'."
    ),
) -> None:
    """Build the self-contained backtest report.html (DESIGN §9.4) from whatever
    `pickem backtest` runs are already on file for these seasons."""
    conn = repository.get_connection()
    out_path = report_viz.generate_backtest_report(conn, harness.parse_seasons(seasons))
    typer.echo(f"Wrote {out_path}")


if __name__ == "__main__":
    app()
