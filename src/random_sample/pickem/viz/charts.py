"""Matplotlib chart generation for DESIGN §9.4's backtest report and §8's live weekly
charts. Backtest charts come back as inline SVG markup (embedded in `report.html`, DESIGN
§10 decision #6, not linked files); the live-path charts (deferred from Milestone A) are
written straight to disk under `reports/<season>/<week>/`, since they're a loose per-week
artifact rather than part of a bundled report."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from random_sample.pickem.aggregation import (  # noqa: E402
    ConfigAggregate,
    MARKET_BASELINE_DESIGN,
    NAIVE_BASELINE_DESIGN,
)
from random_sample.pickem.db import repository  # noqa: E402

ConfigKey = tuple[str, str, str, str]

# Okabe-Ito: a colorblind-safe categorical palette, used consistently across every chart
# in this module instead of matplotlib's default cycle -- this is the first charting code
# in the project, so it doubles as the project's baseline convention going forward.
PALETTE = [
    "#0072B2", "#E69F00", "#009E73", "#D55E00",
    "#CC79A7", "#56B4E9", "#F0E442", "#000000",
]
GRID_COLOR = "#d9d9d9"
TEXT_COLOR = "#333333"


def _style_axes(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID_COLOR)
    ax.spines["bottom"].set_color(GRID_COLOR)
    ax.tick_params(colors=TEXT_COLOR, labelsize=9)
    ax.yaxis.grid(True, color=GRID_COLOR, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.xaxis.label.set_color(TEXT_COLOR)
    ax.yaxis.label.set_color(TEXT_COLOR)
    ax.title.set_color(TEXT_COLOR)


def _fig_to_svg(fig) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    svg = buf.getvalue()
    # report.html embeds this as an inline fragment -- drop matplotlib's XML/DOCTYPE
    # preamble, keep only the <svg>...</svg> element itself.
    return svg[svg.index("<svg"):]


def _grid_shape(n: int, max_cols: int = 3) -> tuple[int, int]:
    ncols = min(n, max_cols) or 1
    nrows = -(-n // ncols)
    return nrows, ncols


def render_leaderboard_html(aggregates: list[ConfigAggregate]) -> str:
    """DESIGN §9.4's leaderboard -- a plain HTML table (not a chart image) so the numbers
    stay selectable/searchable in the bundled report."""
    header = (
        "<tr><th>Rank</th><th>Agent Design</th><th>Prompt Variant</th><th>Model</th>"
        "<th>Context</th><th>Mean Pts</th><th>Std Dev</th><th>Seasons Won</th>"
        "<th>vs Naive</th><th>vs Market</th></tr>"
    )
    rows = []
    for rank, agg in enumerate(aggregates, start=1):
        margin_naive = f"{agg.margin_vs_naive:+.1f}" if agg.margin_vs_naive is not None else "—"
        margin_market = (
            f"{agg.margin_vs_market:+.1f}" if agg.margin_vs_market is not None else "—"
        )
        row_class = ' class="baseline-row"' if agg.is_baseline else ""
        rows.append(
            f"<tr{row_class}><td>{rank}</td><td>{agg.agent_design}</td>"
            f"<td>{agg.prompt_variant}</td><td>{agg.model}</td><td>{agg.context_level}</td>"
            f"<td>{agg.mean_season_points:.1f}</td><td>{agg.stdev_season_points:.1f}</td>"
            f"<td>{agg.seasons_won}/{agg.seasons_played}</td>"
            f"<td>{margin_naive}</td><td>{margin_market}</td></tr>"
        )
    return f'<table class="leaderboard">{header}{"".join(rows)}</table>'


def render_season_trend_svg(
    conn: sqlite3.Connection, seasons: list[int], configs: list[ConfigKey]
) -> str:
    """One small-multiple per season, cumulative backtest points by week, one line per
    config (DESIGN §9.4) -- "who was ahead, and when," per season rather than one
    overloaded chart mixing seasons together."""
    nrows, ncols = _grid_shape(len(seasons))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.5 * ncols, 3.5 * nrows), squeeze=False)
    color_by_config = {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(configs)}

    for idx, season in enumerate(seasons):
        ax = axes[idx // ncols][idx % ncols]
        weekly = repository.backtest_weekly_points(conn, season)
        points_by_config: dict[ConfigKey, dict[int, int]] = {c: {} for c in configs}
        for row in weekly:
            key = (row["agent_design"], row["prompt_variant"], row["model"], row["context_level"])
            if key in points_by_config:
                points_by_config[key][row["week"]] = row["points"]

        for config in configs:
            weeks = sorted(points_by_config[config])
            if not weeks:
                continue
            cumulative, total = [], 0
            for week in weeks:
                total += points_by_config[config][week]
                cumulative.append(total)
            ax.plot(
                weeks, cumulative, label="/".join(config), color=color_by_config[config],
                linewidth=1.8, marker="o", markersize=3,
            )
        ax.set_title(str(season), fontsize=10)
        ax.set_xlabel("Week")
        ax.set_ylabel("Cumulative points")
        _style_axes(ax)

    for idx in range(len(seasons), nrows * ncols):
        axes[idx // ncols][idx % ncols].axis("off")

    handles_by_label = {}
    for row_axes in axes:
        for ax in row_axes:
            for handle, label in zip(*ax.get_legend_handles_labels()):
                handles_by_label.setdefault(label, handle)
    if handles_by_label:
        fig.legend(
            handles_by_label.values(), handles_by_label.keys(), loc="upper center",
            bbox_to_anchor=(0.5, 0.02), ncol=min(len(handles_by_label), 4), fontsize=8, frameon=False,
        )
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    return _fig_to_svg(fig)


def render_calibration_svg(
    conn: sqlite3.Connection, seasons: list[int], configs: list[ConfigKey], n_buckets: int = 5
) -> str:
    """One reliability diagram per config (DESIGN §9.4): predicted win-probability bucket
    vs. actual win rate, with a dashed y=x reference line -- a sanity check that
    confidence values mean something, independent of whether a design scored well."""
    nrows, ncols = _grid_shape(len(configs))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 4 * nrows), squeeze=False)
    bucket_width = 0.5 / n_buckets

    for idx, config in enumerate(configs):
        ax = axes[idx // ncols][idx % ncols]
        rows = repository.backtest_calibration_rows(conn, seasons, *config)
        bucket_correct = [0] * n_buckets
        bucket_total = [0] * n_buckets
        for row in rows:
            p = row["win_probability"]
            bucket = min(int((p - 0.5) / bucket_width), n_buckets - 1) if p >= 0.5 else 0
            bucket_correct[bucket] += row["correct"]
            bucket_total[bucket] += 1

        xs, ys, sizes = [], [], []
        for i in range(n_buckets):
            if bucket_total[i] == 0:
                continue
            xs.append(0.5 + bucket_width * (i + 0.5))
            ys.append(bucket_correct[i] / bucket_total[i])
            sizes.append(20 + bucket_total[i] * 2)

        ax.plot([0.5, 1.0], [0.5, 1.0], linestyle="--", color=GRID_COLOR, linewidth=1, zorder=1)
        if xs:
            ax.scatter(xs, ys, s=sizes, color=PALETTE[0], zorder=2)
        else:
            ax.text(
                0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes,
                color=TEXT_COLOR, fontsize=9,
            )
        ax.set_xlim(0.45, 1.02)
        ax.set_ylim(0.0, 1.02)
        ax.set_title("/".join(config), fontsize=9)
        ax.set_xlabel("Predicted win probability")
        ax.set_ylabel("Actual win rate")
        _style_axes(ax)

    for idx in range(len(configs), nrows * ncols):
        axes[idx // ncols][idx % ncols].axis("off")

    fig.tight_layout()
    return _fig_to_svg(fig)


def render_baseline_comparison_svg(aggregates: list[ConfigAggregate]) -> str:
    """Every non-baseline config's mean season points as a bar, against the naive and
    market baselines' own means plotted as reference lines on the same axis (DESIGN
    §9.4) -- the direct visual for PRD §14's "must beat a naive baseline" bar."""
    real_configs = [a for a in aggregates if not a.is_baseline]
    naive = next((a for a in aggregates if a.agent_design == NAIVE_BASELINE_DESIGN), None)
    market = next((a for a in aggregates if a.agent_design == MARKET_BASELINE_DESIGN), None)

    fig, ax = plt.subplots(figsize=(max(6.0, 1.2 * len(real_configs)), 4.5))
    x = range(len(real_configs))
    ax.bar(x, [a.mean_season_points for a in real_configs], color=PALETTE[0], width=0.6, zorder=2)
    ax.set_xticks(list(x))
    ax.set_xticklabels([a.label for a in real_configs], rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("Mean season points")

    if naive is not None:
        ax.axhline(
            naive.mean_season_points, color=PALETTE[3], linestyle="--", linewidth=1.5,
            label=f"naive_favorite ({naive.mean_season_points:.1f})",
        )
    if market is not None:
        ax.axhline(
            market.mean_season_points, color=PALETTE[2], linestyle="--", linewidth=1.5,
            label=f"market_favorite ({market.mean_season_points:.1f})",
        )
    if naive is not None or market is not None:
        ax.legend(fontsize=8, frameon=False)

    _style_axes(ax)
    fig.tight_layout()
    return _fig_to_svg(fig)


def save_live_calibration_scatter(picks: list[dict], out_path: Path) -> None:
    """DESIGN §8's weekly calibration scatter: predicted probability vs. assigned
    confidence, one point per game in this week's live run."""
    fig, ax = plt.subplots(figsize=(5, 5))
    if picks:
        ax.scatter(
            [p["win_probability"] for p in picks], [p["confidence"] for p in picks],
            color=PALETTE[0], s=40, zorder=2,
        )
    ax.set_xlabel("Predicted win probability")
    ax.set_ylabel("Assigned confidence")
    ax.set_xlim(0.45, 1.02)
    _style_axes(ax)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format="svg", bbox_inches="tight")
    plt.close(fig)


def save_live_season_cumulative(conn: sqlite3.Connection, season: int, out_path: Path) -> None:
    """DESIGN §8's season cumulative-points line chart for the live `recommend` config --
    only reflects weeks `pickem score` has already graded."""
    weekly = repository.live_weekly_points(conn, season)
    fig, ax = plt.subplots(figsize=(6, 4))
    if weekly:
        cumulative, total = [], 0
        weeks = [row["week"] for row in weekly]
        for row in weekly:
            total += row["points"]
            cumulative.append(total)
        ax.plot(weeks, cumulative, color=PALETTE[0], marker="o", linewidth=1.8)
    else:
        ax.text(
            0.5, 0.5, "No scored weeks yet this season", ha="center", va="center",
            transform=ax.transAxes, color=TEXT_COLOR, fontsize=10,
        )
    ax.set_xlabel("Week")
    ax.set_ylabel("Cumulative points")
    ax.set_title(f"{season} season-to-date")
    _style_axes(ax)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format="svg", bbox_inches="tight")
    plt.close(fig)
