# Design: Yahoo Pro Pick'em Recommendation Agent

Status: Draft v0.5 — no open decisions remain (see §10); companion to `PRD.md`, translating product
requirements into concrete architecture. Assumes familiarity with PRD §9 (Architecture) and §10
(Backtesting Harness).

## 1. Scope

This document covers implementation-level design: package layout, the LangGraph state/graph
definition, the SQLite schema, the CLI surface, the Context Agent's feature tiers, the Ollama
integration, and how the backtesting harness executes its three-variable grid (PRD §10).

## 2. Package Layout

```
src/random_sample/
  pickem/
    __init__.py
    cli.py              # Typer CLI: recommend, score, backtest
    graph.py             # LangGraph StateGraph construction & compilation
    state.py             # PickemState and related TypedDicts
    dates.py             # current-week resolution, used by cli.py for default --week/--season
    nflverse_client.py   # cache-first nflverse fetch (schedule + scores, see 5.3); used by the
                          # Schedule Agent node and directly by `score` (no graph run for scoring)
    nodes/
      schedule.py         # Schedule Agent
      context.py          # Context Agent + feature-tier definitions
      prediction.py        # Prediction Agent node: dispatches to a registered PredictionStrategy
      ranking.py          # Confidence-Ranking Agent
      validator.py        # Validator Agent
      reporting.py        # Reporting Agent: tables + charts + persistence
    strategies/
      __init__.py                    # PredictionStrategy protocol + STRATEGY_REGISTRY
      single_analyst.py              # one LLM call per game, full context
      specialist_synthesis.py        # fan-out stats/situational/market calls + LLM synthesis
      specialist_deterministic.py    # same fan-out, code-level weighted average (no synthesis LLM call)
      debate_advocate.py             # two opposing advocates + a judge
      ensemble_self_consistency.py   # N samples of the single-analyst prompt, voted/averaged
    prompts/
      __init__.py          # PromptTemplate, PromptSet, PROMPT_SET_REGISTRY
      single_analyst_v1.py   # reused by ensemble_self_consistency (same role: "analyst")
      specialist_v1.py       # bundles stats/situational/market (+ synthesis) templates
      debate_v1.py           # advocate_home/advocate_away/judge templates
    db/
      schema.sql           # DDL
      repository.py        # thin data-access layer over sqlite3
    backtest/
      harness.py           # grid runner over (agent_design, prompt_variant, model, context_level) x (season, week)
      cache.py             # LLM response cache keyed by content hash
    viz/
      charts.py             # matplotlib chart builders
```

## 3. LangGraph State & Graph

### 3.1 State schema

```python
class RunConfig(TypedDict):
    agent_design: str          # key into STRATEGY_REGISTRY, e.g. "single_analyst", "specialist_synthesis"
    prompt_variant: str        # key into PROMPT_SET_REGISTRY; must cover every role the chosen
                               # strategy needs (checked at graph-build time, not mid-run)
    model: str                 # Ollama model name, e.g. "llama3.1:8b"
    context_level: Literal["minimal", "standard", "rich"]

class Game(TypedDict):
    game_id: str
    season: int
    week: int
    home_team: str
    away_team: str
    kickoff_ts: str

class GameContext(TypedDict):
    game_id: str
    features: dict             # shape depends on context_level
    context_hash: str          # sha256 of sorted-key JSON, drives cache + reproducibility

class AgentPrediction(TypedDict):
    game_id: str
    agent_role: str             # "analyst" | "stats" | "injuries" | "market" | "synthesis"
    predicted_winner: str
    win_probability: float
    rationale: str

class Pick(TypedDict):
    game_id: str
    predicted_winner: str
    win_probability: float
    confidence: int              # 1..N

class PickemState(TypedDict):
    run_id: str
    season: int
    week: int
    config: RunConfig
    games: list[Game]
    context: dict[str, GameContext]          # game_id -> context
    raw_predictions: dict[str, list[AgentPrediction]]   # game_id -> per-agent-role outputs
    predictions: dict[str, AgentPrediction]  # game_id -> post-synthesis, one per game
    picks: list[Pick]
    validation_errors: list[str]
    retries: int
```

### 3.2 Graph topology

```
schedule -> context -> prediction -> rank -> validate --[invalid, retries < 3]--> rank
                                              validate --[valid]--------------> report
                                              validate --[invalid, retries >= 3]--> report (flagged)
```

- **prediction** looks up `config.agent_design` in `STRATEGY_REGISTRY` (§3.3) and delegates to
  whatever that strategy does internally — anywhere from one prompt per game to several fanned-out
  calls plus a combining step. The node itself has no branching logic of its own.
- **validate** checks: every game has exactly one pick, confidence values are a permutation of
  `1..N`, probabilities are in `[0,1]`. A ranking-level failure loops back to `rank`; a missing/
  malformed prediction loops back to `prediction`. `retries` is incremented on every loop.
- After 3 failed attempts, `report` still runs (so a run never silently vanishes) but the `runs`
  row is written with `status='failed_validation'` so it's visibly excluded from "valid" comparisons.

### 3.3 Strategy & Prompt Registries

`agent_design` and `prompt_variant` are open string identifiers rather than a fixed `Literal`,
resolved at runtime against two registries — so trying a new agent design or prompt means
registering it, not editing `state.py` or `graph.py`.

```python
class PredictionStrategy(Protocol):
    name: str
    required_roles: tuple[str, ...]   # e.g. ("analyst",) or ("stats","situational","market","synthesis")

    def predict(
        self,
        games: list[Game],
        context: dict[str, GameContext],
        config: RunConfig,
        prompts: "PromptSet",
    ) -> dict[str, AgentPrediction]: ...

STRATEGY_REGISTRY: dict[str, type[PredictionStrategy]] = {}

def register_strategy(name: str):
    def decorator(cls):
        STRATEGY_REGISTRY[name] = cls
        return cls
    return decorator
```

**v1 registered strategies** (the `agent_design` values the backtest grid sweeps over):

| `agent_design` | `required_roles` | Shape |
|---|---|---|
| `single_analyst` | `("analyst",)` | One prompt per game, full context. Baseline. |
| `specialist_synthesis` | `("stats","situational","market","synthesis")` | Three specialists (each sees only its data slice) fan into an LLM synthesis call. |
| `specialist_deterministic` | `("stats","situational","market")` | Same three specialists; probabilities combined by a fixed weighted average in code — no synthesis LLM call. Isolates whether synthesis earns its cost over `specialist_synthesis`. |
| `debate_advocate` | `("advocate_home","advocate_away","judge")` | Two agents each argue for one team using the full context; a judge agent sees both arguments and decides. |
| `ensemble_self_consistency` | `("analyst",)` | Reuses the `single_analyst` prompt, called N times (optionally across different models), voted/averaged in code. |

Roles map to data as follows: **stats** = record, point differential, EPA/play, recent form;
**situational** = rest days, home/away, divisional game, surface/roof; **market** = nflverse's own
spread/total/moneyline fields (also the naive baseline PRD §14 requires beating). An **injuries**
role is a deliberate v1 omission — noisier data coverage — and can be added later as a new
registered role without touching any existing strategy.

This is a real expansion of the backtest grid (5 `agent_design` values, not 2), which sharpens
PRD §14's runtime risk: running the full cross-product (5 designs × N models × 3 context levels ×
however many historical weeks) is likely to be impractical on local hardware for a first pass.
Treat "run a targeted subset, not the full grid" (already noted as acceptable in PRD §14) as the
expected default for early iterations, widening the sweep once the harness and cache are proven
out.

```python
class PromptSet(TypedDict):
    id: str
    templates: dict[str, str]   # role -> prompt template

PROMPT_SET_REGISTRY: dict[str, PromptSet] = {}
```

- The `prediction` node resolves `config.agent_design` → strategy class and `config.prompt_variant`
  → `PromptSet`, then checks the prompt set covers every role in the strategy's `required_roles`.
  An incompatible pair (e.g. a `single_analyst`-shaped prompt set paired with
  `specialist_synthesis`) is a **hard error** for `pickem recommend`, and a
  **skipped-with-warning** grid cell for `pickem backtest` sweeps — a sweep is expected to
  enumerate combinations, not all of which are valid, and shouldn't abort the whole run over one
  bad cell.
- Registration happens via decorator on import; `strategies/__init__.py` and `prompts/__init__.py`
  import every module in their package so registration runs once at startup. `nodes/prediction.py`
  never hardcodes which strategies or prompts exist — it only knows how to look one up.
- Adding a new agent design or prompt variant is: add a file under `strategies/` or `prompts/`,
  register it, then reference its name from `config.toml` or a `--agent-design`/`--prompt-variant`
  CLI flag. No changes to `graph.py`, `state.py`, or the backtest harness.

## 4. SQLite Schema

```sql
CREATE TABLE runs (
    run_id          TEXT PRIMARY KEY,
    run_type        TEXT NOT NULL CHECK (run_type IN ('live','backtest')),
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    agent_design    TEXT NOT NULL,
    prompt_variant  TEXT NOT NULL,
    model           TEXT NOT NULL,
    context_level   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ok'   -- 'ok' | 'failed_validation'
);

CREATE TABLE games (
    game_id      TEXT PRIMARY KEY,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    home_team    TEXT NOT NULL,
    away_team    TEXT NOT NULL,
    kickoff_ts   TEXT NOT NULL,
    home_score   INTEGER,
    away_score   INTEGER
);

CREATE TABLE week_sync (
    season           INTEGER NOT NULL,
    week             INTEGER NOT NULL,
    synced_final     INTEGER NOT NULL DEFAULT 0,  -- 1 once every game in the week has a final score
    last_synced_at   TEXT NOT NULL,
    PRIMARY KEY (season, week)
);

CREATE TABLE picks (
    run_id             TEXT NOT NULL REFERENCES runs(run_id),
    game_id            TEXT NOT NULL REFERENCES games(game_id),
    predicted_winner   TEXT NOT NULL,
    win_probability    REAL NOT NULL,
    confidence         INTEGER NOT NULL,
    PRIMARY KEY (run_id, game_id)
);

CREATE TABLE scores (
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    game_id         TEXT NOT NULL REFERENCES games(game_id),
    correct         INTEGER NOT NULL,   -- 0/1
    points_earned   INTEGER NOT NULL,
    PRIMARY KEY (run_id, game_id)
);

CREATE TABLE llm_cache (
    cache_key       TEXT PRIMARY KEY,   -- hash(game_id, context_hash, agent_design, prompt_variant, model, agent_role)
    game_id         TEXT NOT NULL,
    response_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
```

- Live and backtest runs share the same tables, distinguished by `runs.run_type`. A live week
  produces exactly one `runs` row (the promoted production config); a backtest sweep produces one
  `runs` row per grid cell per (season, week).
- `llm_cache` is shared across live and backtest — an identical (game, context, agent design,
  prompt variant, model, role) tuple is only ever sent to Ollama once, live or backtest. Including
  `prompt_variant` in the key matters: swapping a prompt's wording must produce a different cache
  entry, never silently reuse a response generated under different instructions.
- Season totals and cross-design comparisons (PRD §10, §12) are derived with `SUM`/`GROUP BY`
  queries over `scores` joined to `runs`, not stored redundantly.

## 5. CLI Surface

```
pickem recommend [--week 3] [--season 2026]
    # live: runs the graph once with the production config (from config.toml),
    # prints the picks table, writes charts, persists to SQLite.
    # --week/--season are optional — see 5.1 for the default when omitted.

pickem score [--week 3] [--season 2026]
    # ingests final results for the week, computes points, updates season totals.
    # --week/--season are optional — see 5.1 for the default when omitted.

pickem backtest --seasons 2022-2025 \
                 --agent-design single_analyst,specialist_synthesis,specialist_deterministic \
                 --prompt-variant v1 \
                 --model llama3.1:8b,qwen2.5:14b \
                 --context-level minimal,standard,rich
    # sweeps the grid (a subset of the full registry, not necessarily all 5 designs
    # at once — see 3.3 on why the full cross-product is likely impractical at
    # first), skipping any agent-design/prompt-variant pair the registry marks
    # incompatible (logging a warning), writes backtest_runs rows, and produces
    # comparison charts.
```

`recommend`'s production config (one `agent_design` × `prompt_variant` × `model` ×
`context_level`) lives in a small `config.toml`, set by hand after reviewing backtest results —
never hardcoded, and never itself inferred automatically from the backtest (promotion is a
manual, reviewed step).

### 5.1 Default Week Resolution

`--week`/`--season` are optional on `recommend` and `score`; `backtest` always requires explicit
`--seasons` since a historical sweep has no "current" week. `dates.py` provides one primitive:

```python
def current_week(schedule: list[Game], now: datetime) -> tuple[int, int]:
    """(season, week) of the earliest game with kickoff_ts >= now; falls back to the
    last available (season, week) in the schedule if now is past everything loaded."""
    upcoming = [g for g in schedule if g.kickoff_ts >= now]
    game = min(upcoming, key=lambda g: g.kickoff_ts) if upcoming else max(schedule, key=lambda g: g.kickoff_ts)
    return game.season, game.week
```

- `now` is an injected parameter (defaulting to `datetime.now()` only at the `cli.py` call site),
  not read from the system clock inside `dates.py` — keeps the function trivially testable with a
  pinned date.
- **`recommend` defaults to `current_week(schedule, now)`** — the week containing the next game
  that hasn't kicked off yet, i.e. the week you still need to submit picks for.
- **`score` defaults to `current_week(schedule, now)` minus one week** (same season, or the prior
  season's final week if currently at week 1) — the most recently *completed* week, not the
  upcoming one. This matters: right after Monday Night Football ends, `current_week` has already
  rolled over to next week, but there's nothing to score there yet — last week is what needs
  grading.
- v1 assumes the regular season (weeks 1–18); resolving a "current week" during the offseason or
  postseason is out of scope and can raise a clear error rather than guessing.

### 5.2 How `score` Updates the Database

`score` does **not** run the LangGraph pipeline — there's nothing to predict, only results to
grade, so it's a lightweight path that reuses `nflverse_client.py` directly:

1. Fetch the current schedule/results for the target season from nflverse (the same client the
   Schedule Agent uses for `recommend`).
2. Upsert `games.home_score`/`away_score` for that week's games wherever nflverse now reports a
   final score (still `NULL` for games not yet final).
3. Look up the `runs` row for that (season, week) with `run_type='live'` — the one `recommend`
   already created — and its `picks`.
4. For every game that's now final, compute `correct`/`points_earned` and upsert into `scores`;
   games still in progress are left unscored.
5. Print a summary (e.g. "11/13 games final, 87 points so far, 2 pending") and regenerate the
   season-to-date chart.

`score` is **idempotent** — safe to re-run repeatedly as a Sunday progresses (upserting, never
appending), so there's no need for a separate "sync" command the user has to remember to run
first, and no risk of double-counting from re-running it. Running `recommend` never touches
results, and running `score` never touches picks — the two stay cleanly separated.

### 5.3 Respecting the Data Source: Cache-First Fetching

Both the Schedule Agent (for `recommend`) and `score` need nflverse data, but neither should hit
the network on every invocation — once a week's games are all final, that result can never change,
so there's nothing left to ask nflverse about. `nflverse_client.py` exposes one entry point that
both funnel through:

```python
def ensure_week(season: int, week: int) -> list[Game]:
    """Local-first: only calls nflverse if this week isn't already fully resolved."""
    if week_sync_is_final(season, week):        # week_sync.synced_final == 1
        return games_from_db(season, week)       # no network call at all

    schedule = nflverse_fetch_schedule(season)    # one remote call, whole season's data
    upsert_games(schedule)                        # store/refresh every week's rows, not just this one
    mark_synced_weeks_as_final(season, schedule)  # set week_sync.synced_final for any week now fully final
    return games_from_db(season, week)
```

- **Check local first**: if `week_sync` already says this week is `synced_final`, `ensure_week`
  returns straight from SQLite — the "download once" case the whole cache exists for.
- **If not found (or not yet final), fetch and store**: exactly one remote call per invocation,
  covering the whole season (that's the natural granularity nflverse returns data at) — its
  result updates every week's rows in `games`, and `week_sync` is recomputed for all of them, so
  other weeks that just became final are cached too, not only the one that was asked for.
- A live week in progress is fetched at most once per command invocation — never polled or
  retried within a single run — and once it's fully final, it's never fetched again, live or
  during a `backtest` replay of that season.
- This is on top of whatever caching `nflreadpy` itself does internally; treat this DB-level cache
  as the authoritative layer we control, rather than relying on the library's own behavior.

## 6. Context Agent: Feature Tiers (starting proposal)

- **minimal** — current win-loss record, current point differential, home/away.
- **standard** — + last-5-games form (win%, point differential), rest days since last game,
  season EPA/play (offense & defense).
- **rich** — + head-to-head history (last N meetings), betting line (spread/moneyline) if present
  in nflverse's betting-lines dataset, injury report on a best-effort basis (nflverse's injury
  data completeness varies by season).

Each tier's `features` dict is serialized with sorted keys before hashing
(`context_hash = sha256(json.dumps(features, sort_keys=True))`), so identical inputs always
produce identical cache keys — this is what makes both the LLM cache and backtest reproducibility
work. Exact field lists are expected to be refined once the harness is running; only the tiering
concept and the hashing contract are load-bearing.

Any rolling/aggregate field here (recent form, EPA, rest days) must be computed point-in-time
during a backtest — see §9.1's walk-forward constraint — never from a season's complete data.

## 7. Prediction Agent: Ollama Integration

- Uses `langchain-ollama`'s `ChatOllama` (or the `ollama` Python client directly) against a local
  server (`http://localhost:11434` by default).
- Requests structured output (JSON mode / tool-calling where the model supports it, otherwise a
  strict prompt with parse-and-retry) matching `AgentPrediction` minus `game_id`/`agent_role`
  (those are filled in by the node, not the model).
- Temperature fixed low (e.g. `0.1`) for reproducibility. Combined with `llm_cache`, a given
  (game, context, design, model, role) tuple is computed at most once.

## 8. Reporting Agent

- **CLI table**: formatted (e.g. via `rich`), sorted by confidence descending — winner,
  confidence, win probability.
- **Charts** (matplotlib, saved under `reports/<season>/<week>/`): weekly calibration scatter
  (predicted probability vs. assigned confidence), season cumulative-points line chart; for
  backtests, a comparison chart across grid cells.
- **Persistence**: upserts `games`, writes `runs` + `picks`; `score` later fills
  `games.home_score/away_score` and writes `scores`.

## 9. Evaluation Methodology

Answers the question the design hadn't yet: given several historical seasons and a grid of
candidate configs (agent design × model × context level × prompt variant, §3.3), how do we
systematically decide which config gets promoted to `config.toml` for the live season?

### 9.1 Walk-Forward Simulation (per season, week by week)

For each backtested season and each regular-season week 1–18, in order:

1. Assemble that week's slate and context **using only information that would have been available
   before the week's earliest kickoff** — recent-form/EPA aggregates, rest days, and any other
   rolling feature must be computed from games completed strictly before that week, never
   including it or later weeks. This point-in-time boundary is what separates a real evaluation
   from an inflated one; computing "recent form" from the full season's final stats would silently
   invalidate every result derived from it. (Applies to the feature side only — the historical
   *labels*, i.e. final scores, are already fully known and safe to score against.)
2. Run every config being evaluated through the identical live graph (§10 decision #1: backtest
   reuses the live graph) against that slate.
3. Score each config's picks against that week's actual result and persist one
   `backtest_runs`-equivalent row (season, week, config, points_earned).
4. Move to the next week, carrying forward a running season-to-date total per config.

Simulating week by week, rather than scoring a whole season in one shot, is what makes "which
design would have won that season" a meaningful question — it reproduces the actual conditions a
live season imposes, including early weeks having less prior-season-independent data to work with
than late ones.

### 9.2 Cross-Design Comparison Within a Season

Every tested config is treated as an independent entrant in the same pick'em pool for that season.
A SQL view over `scores`/`runs` derives the comparison — no new table needed, consistent with how
live season totals are already computed on demand rather than stored redundantly:

```sql
CREATE VIEW season_summary AS
SELECT season, agent_design, prompt_variant, model, context_level,
       SUM(points_earned) AS season_points,
       RANK() OVER (PARTITION BY season ORDER BY SUM(points_earned) DESC) AS season_rank
FROM scores JOIN runs USING (run_id)
WHERE runs.run_type = 'backtest'
GROUP BY season, agent_design, prompt_variant, model, context_level;
```

`season_rank = 1` directly answers "which design would have won that season."

### 9.3 Cross-Season Aggregation

Any single season is a small, noisy sample, so no config is promoted on one season's result alone.
Across every season tested, compute per config:

- **Mean season points** — the headline comparison number.
- **Standard deviation across seasons** — consistency, not just peak performance.
- **Seasons won** — count of seasons where `season_rank = 1`.
- **Margin vs. baselines** — mean season points minus a naive-favorite baseline ("always pick the
  recorded favorite") and the market-favorite baseline (the `market` role's own signal, already
  planned as a specialist). A config that can't beat the market baseline on average isn't a
  promotion candidate, regardless of how it ranks against other LLM-based designs — this is the
  concrete, measurable form of PRD §14's "must beat a naive baseline" risk.

### 9.4 Reports

`viz/charts.py` produces one **self-contained `report.html`** per backtest batch under
`reports/backtest/<batch_id>/` — a single styled artifact bundling:

- **Leaderboard** — every tested config ranked by mean season points, with seasons won, standard
  deviation, and margin vs. both baselines in the same row.
- **Season trend charts** — one small-multiple per season, cumulative points by week, one line per
  config — the direct visual for "who was ahead, and when," rather than one overloaded chart
  mixing seasons together.
- **Calibration (reliability) chart** per config — predicted win-probability bucket vs. actual win
  rate — a sanity check that confidence values mean something, independent of whether a design
  happened to score well.
- **Baseline comparison chart** — every config's mean season points plotted against the naive and
  market baselines on the same axis.

Charts are generated with matplotlib and embedded (inline SVG/PNG) directly in the HTML rather
than left as separate files — one artifact to open, archive, or hand to someone else. When this is
actually built, it should follow the project's established data-visualization conventions for a
consistent, professional look rather than library defaults.

## 10. Decisions (Locked)

All decisions raised during design review are confirmed as proposed:

1. **Backtest reuses the exact live graph**, just swapping `RunConfig` — never a separate,
   lighter-weight backtest path. This is the only way a backtest result actually predicts live
   behavior.
2. **CLI framework**: `typer` — thin, type-hint driven, pairs well with the `TypedDict` config.
3. **DB normalization**: `scores` is a separate table from `picks` rather than computed only at
   query time — cheap season-total queries and a durable record even if scoring logic changes
   later.
4. **Feature-tier content** (§6) stands as a placeholder — refine once implementation shows what
   nflverse actually exposes cleanly, rather than nailing it down now.
5. **Incompatible agent-design/prompt-variant pairs** (§3.3): hard-error for `recommend`,
   skip-with-warning for `backtest` sweeps.
6. **Evaluation reports** (§9.4): a single self-contained `report.html` per backtest batch, not a
   folder of loose chart files.

No open decisions remain — DESIGN.md is ready to drive a task breakdown.
