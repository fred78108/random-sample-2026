"""Self-contained `report.html` generator (DESIGN §9.4, §10 decision #6): one styled
artifact per backtest batch under `reports/backtest/<batch_id>/`, bundling the leaderboard,
season trend charts, calibration charts, and baseline comparison chart as inline HTML/SVG
rather than a folder of loose chart files."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from random_sample.pickem import aggregation
from random_sample.pickem.viz import charts

DEFAULT_OUTPUT_ROOT = Path("reports/backtest")

_STYLE = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 2rem; color: #222; background: #fff; }
h1 { margin-bottom: 0.2rem; }
.subtitle { color: #666; margin-top: 0; margin-bottom: 2rem; }
h2 { margin-top: 2.5rem; border-bottom: 1px solid #ddd; padding-bottom: 0.3rem; }
table.leaderboard { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
table.leaderboard th, table.leaderboard td { padding: 0.4rem 0.7rem; text-align: right;
    border-bottom: 1px solid #eee; }
table.leaderboard th:nth-child(2), table.leaderboard td:nth-child(2),
table.leaderboard th:nth-child(3), table.leaderboard td:nth-child(3),
table.leaderboard th:nth-child(4), table.leaderboard td:nth-child(4),
table.leaderboard th:nth-child(5), table.leaderboard td:nth-child(5) { text-align: left; }
table.leaderboard tr.baseline-row { background: #f5f5f5; font-style: italic; }
.chart-block { max-width: 100%; overflow-x: auto; }
.chart-block svg { max-width: 100%; height: auto; }
.empty { color: #888; font-style: italic; }
"""


def generate_backtest_report(
    conn: sqlite3.Connection,
    seasons: list[int],
    batch_id: str | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> Path:
    """Build the report for `seasons` and write it to
    `<output_root>/<batch_id>/report.html`. Returns the written path."""
    aggregates = aggregation.aggregate_configs(conn, seasons)
    batch_id = batch_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = output_root / batch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "report.html"

    if not aggregates:
        out_path.write_text(
            f"<!doctype html><html><head><title>Backtest Report</title>"
            f"<style>{_STYLE}</style></head><body>"
            f"<h1>Backtest Report</h1>"
            f'<p class="subtitle">Seasons: {", ".join(map(str, seasons))}</p>'
            f'<p class="empty">No backtest runs found for these seasons.</p>'
            f"</body></html>"
        )
        return out_path

    configs = [
        (a.agent_design, a.prompt_variant, a.model, a.context_level) for a in aggregates
    ]
    leaderboard_html = charts.render_leaderboard_html(aggregates)
    season_trend_svg = charts.render_season_trend_svg(conn, seasons, configs)
    calibration_svg = charts.render_calibration_svg(conn, seasons, configs)
    baseline_svg = charts.render_baseline_comparison_svg(aggregates)

    out_path.write_text(
        f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Backtest Report — {batch_id}</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>Backtest Report</h1>
<p class="subtitle">Batch {batch_id} — seasons {", ".join(map(str, seasons))}</p>

<h2>Leaderboard</h2>
{leaderboard_html}

<h2>Season Trends</h2>
<div class="chart-block">{season_trend_svg}</div>

<h2>Calibration</h2>
<div class="chart-block">{calibration_svg}</div>

<h2>Config vs. Baselines</h2>
<div class="chart-block">{baseline_svg}</div>

</body>
</html>
"""
    )
    return out_path
