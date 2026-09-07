# Tasks: Yahoo Pro Pick'em Recommendation Agent

Status: Draft v0.1 — derived from `DESIGN.md` v0.5. Organized as milestones in build order, not a
flat backlog: each milestone produces something runnable before the next one adds scope, so there's
a working `recommend` long before the full 5-strategy backtest grid exists.

## Milestone A — Vertical Slice (one design, one live week)

Goal: `pickem recommend` produces real picks for a real week, end to end, using only
`single_analyst`. Proves the architecture before multiplying it by 5 strategies × N models ×
3 context levels.

- [x] Scaffold `src/random_sample/pickem/` package layout (DESIGN §2).
- [x] Add dependencies to `pyproject.toml`: `langgraph`, `langchain-ollama`, `typer`, `matplotlib`.
      (Also added `rich`, needed by `nodes/reporting.py`'s CLI table — not called out explicitly
      in this bullet but required by a later one below.)
- [x] `db/schema.sql` — `runs`, `games`, `week_sync`, `picks`, `scores`, `llm_cache` tables and the
      `season_summary` view (DESIGN §4, §9.2). `db/repository.py` — thin access functions.
- [x] `nflverse_client.py` — `ensure_week(season, week)` cache-first fetch (DESIGN §5.3). Also
      added `ensure_season(season)`, factored out of the same cache logic, since `cli.py` needs a
      full-season schedule to resolve a default `--week`/`--season` before either is known.
- [x] `dates.py` — `current_week(schedule, now)` (DESIGN §5.1).
- [x] `state.py` — `RunConfig`, `Game`, `GameContext`, `AgentPrediction`, `Pick`, `PickemState`
      (DESIGN §3.1).
- [x] `strategies/__init__.py` — `PredictionStrategy` protocol, `STRATEGY_REGISTRY`,
      `register_strategy` decorator (DESIGN §3.3).
- [x] `prompts/__init__.py` — `PromptSet`, `PROMPT_SET_REGISTRY` (DESIGN §3.3).
- [x] `nodes/schedule.py` — Schedule Agent, calls `nflverse_client.ensure_week`.
- [x] `nodes/context.py` — Context Agent; implement the **minimal** feature tier only for this
      milestone (DESIGN §6) — record, point differential, home/away. Computed strictly from games
      completed before the target week, so this is already point-in-time-safe for Milestone D.
- [x] `prompts/single_analyst_v1.py` + `strategies/single_analyst.py` (DESIGN §3.3 table, row 1).
- [x] Ollama integration: structured-output call wrapper, temperature `0.1`, `llm_cache`
      read-through (DESIGN §7). Uses `with_structured_output(..., method="function_calling")` —
      tested against the locally running Ollama server and found `method="json_schema"` doesn't
      reliably produce parseable JSON on the available model, while `function_calling` does.
- [x] `nodes/prediction.py` — registry dispatch node.
- [x] `nodes/ranking.py` — Confidence-Ranking Agent (FR1: rank by win probability, assign `N..1`).
- [x] `nodes/validator.py` — permutation/probability checks + retry-loop routing (DESIGN §3.2).
      Unit-verified all four routing branches (malformed-prediction→`prediction`,
      ranking-failure→`rank`, exhausted-retries→`report`, valid→`report`).
- [x] `nodes/reporting.py` — CLI table (via `rich`) + `runs`/`picks` persistence. Charts deferred
      to Milestone E.
- [x] `graph.py` — wire the topology: `schedule → context → prediction → rank → validate → report`
      with the conditional retry edge.
- [x] `cli.py` — `pickem recommend [--week] [--season]` using `typer`, defaulting via `dates.py`
      (DESIGN §5.1), reading the production config from `config.toml`.
- [x] `config.toml` — hardcode `single_analyst` / `single_analyst_v1` / one Ollama model /
      `minimal` as a placeholder production config (real promotion happens in Milestone F). Model
      is currently a `:cloud`-routed Ollama model (`glm-5.3-flash:cloud`), not a true local one —
      the only models pulled on this machine — per user decision, to prove the wiring end to end
      now. **Swap for a real local model before any live/production use**; a cloud model breaks
      the design's no-data-leaves-the-machine/no-per-call-cost intent (DESIGN §7, PRD §11).
- [x] Manual smoke test: run `pickem recommend` for a real upcoming week against a running local
      Ollama instance; confirm a valid, correctly-ranked pick set prints and persists. Ran for
      Week 1, 2026 (the real next upcoming week) — 16 games, valid picks, confirmed persisted to
      `runs`/`picks`/`games`/`llm_cache` in `pickem.db`, and confirmed a second run hit the cache
      (no new LLM calls, ~1s) instead of recomputing.

## Milestone B — Result Ingestion

Goal: close the loop — grade a week's picks against real outcomes.

- [x] `pickem score [--week] [--season]` (DESIGN §5.2) — bypasses the graph entirely; reuses
      `nflverse_client.ensure_week` + `db/repository.py` directly. New `scoring.py` module holds
      the orchestration (`score_week`); `cli.py` wires it up and turns `NoLiveRunError` into a
      clean `typer.BadParameter` instead of a traceback.
- [x] Scoring logic: for each final game in the target week, compute `correct`/`points_earned`
      against the live `runs` row's `picks`; upsert into `scores` (idempotent — safe to re-run).
      A tied final score is graded as final-but-incorrect (nobody can "correctly" pick a tie under
      N-ranking) rather than left pending.
- [x] Partial-week handling: games without a final score are skipped and reported as pending, not
      treated as an error.
- [x] `score` default week = `current_week() - 1`, distinct from `recommend`'s default (DESIGN
      §5.1) — added `dates.previous_week` (wraps to the prior season's week 18 at week 1) and a
      unit test asserting the two defaults actually differ (`tests/pickem/test_dates.py`).
- [x] Season-to-date summary printed after scoring (sum of `points_earned` for the season so far).
      Added `pytest` as a dev dependency (none existed before this milestone) plus
      `tests/pickem/{test_dates,test_scoring}.py`; also manually smoke-tested against the real
      Week 1, 2026 live run in `pickem.db` — reports 0/16 final, 16 pending (correct — season
      hasn't started), and confirmed `score`'s default (2025 wk 18, no live run there) correctly
      differs from `recommend`'s default (2026 wk 1) and errors cleanly rather than guessing.

## Milestone C — Remaining Strategies & Prompt Sets

Goal: all 5 registered `agent_design` values exist and are individually runnable via `recommend`
before any of them are compared at scale.

- [x] `nodes/context.py` — add **standard** and **rich** feature tiers (recent form/EPA/rest days;
      then head-to-head/betting line/injuries) (DESIGN §6). EPA/play is derived from
      `nflreadpy.load_team_stats` (passing_epa+rushing_epa over attempts+carries, offense and
      defense-allowed, filtered strictly to weeks before the target — point-in-time safe for
      Milestone D). Head-to-head is best-effort like injuries: it only sees meetings already
      cached in the local `games` table, so cross-season history is as complete as whatever
      seasons have been synced. Both `load_team_stats` and `load_injuries` fail soft (empty/null
      features, not an exception) when nflverse hasn't published a season's file yet — hit
      immediately testing against 2026 (season not started); confirmed against 2025 week 10 that
      real numbers come through once prior weeks exist.
- [x] Extended `games` schema (`home_rest`, `away_rest`, `div_game`, `roof`, `surface`,
      `spread_line`, `home_moneyline`, `away_moneyline`, `total_line`) — all already present on
      nflverse's own schedule rows (`load_schedules`), so `nflverse_client.py` needed no new
      fetch, just wider columns on the existing one. Pre-Milestone-C `pickem.db` doesn't have
      these columns; since sqlite3 has no `ADD COLUMN IF NOT EXISTS`, added a runtime migration
      (`repository._migrate_games_table`, checked via `PRAGMA table_info`) that runs every
      `get_connection` instead of a static schema.sql statement — verified against the real
      `pickem.db` from Milestone A/B.
- [x] Added **situational** and **market** feature builders (rest days, home/away, divisional,
      surface/roof; nflverse's own spread/total/moneyline fields) — needed by every specialist
      strategy below. Implemented as `situational_features`/`market_features` in
      `nodes/context.py`, each an isolated slice (no score/opponent data) — `situational` is
      folded into the merged features dict starting at the **standard** tier, `market` starting
      at **rich**, so a specialist role only sees its slice if the run's `context_level` actually
      unlocks it (a `specialist_*` run at `context_level=minimal` degrades to thin/empty
      specialist input rather than erroring — a legitimate grid cell, not a bug).
- [x] `prompts/specialist_v1.py` + `strategies/specialist_synthesis.py` (`stats`, `situational`,
      `market`, `synthesis` roles). The `stats` role's slice is "the merged features dict minus
      the situational/market/head_to_head/injury sub-keys" rather than a separately maintained
      builder, so it stays in sync with whatever `nodes/context.py` puts in each tier
      automatically. Fan-out logic (`SpecialistCaller`) factored out so `specialist_deterministic`
      reuses the exact same three specialist calls.
- [x] `strategies/specialist_deterministic.py` — same three specialists, fixed weighted-average
      combiner in code, no synthesis LLM call. Weights are equal (1/3 each) — a deliberate
      placeholder per DESIGN §10 decision #4, easy to bias later once real calibration data
      exists. Note: because the `llm_cache` key includes `agent_design` (DESIGN §4), the three
      specialist calls are *not* shared with `specialist_synthesis` even though the prompts and
      inputs are identical — each design re-asks Ollama independently, exactly as the locked
      cache-key spec implies.
- [x] `prompts/debate_v1.py` + `strategies/debate_advocate.py` (`advocate_home`, `advocate_away`,
      `judge` roles). Advocates see the full merged context (not a role-specific slice) and are
      prompted to argue for their assigned team while still reporting an honest win probability;
      the judge is explicitly told a confident-sounding case isn't necessarily a strong one.
- [x] `strategies/ensemble_self_consistency.py` — N calls (`ENSEMBLE_SIZE = 5`, a placeholder;
      RunConfig has no ensemble-size field) against the `single_analyst` prompt, combined by
      averaging each sample onto a P(home-wins) scale. Had to tag each sample's `agent_role` as
      `analyst_sample_{i}` rather than the plain `"analyst"` role the prompt set declares:
      `llm_cache`'s key has no sample index, so N calls under the same role would all hit one
      cached response after the first and collapse the whole ensemble into a single sample —
      silently defeating self-consistency. `combine_by_average`/`prob_home_wins` (shared with
      `specialist_deterministic`) added to `strategies/__init__.py`.
- [x] Registry compatibility check: prompt set must cover a strategy's `required_roles` at
      graph-build time; hard-error for `recommend` on mismatch (DESIGN §3.3, §10 decision #5).
      Already implemented in `nodes/prediction.py` as of Milestone A (built generically ahead of
      need); this milestone just exercised it — confirmed it raises `ValueError` naming the
      missing roles when `specialist_synthesis` is paired with `single_analyst_v1`.
- [x] Manual smoke test: run `recommend` once per strategy against the same week, confirm each
      produces a valid pick set (not yet compared for quality — that's Milestone D/E). Ran all 5
      strategies node-by-node (context → prediction → rank → validate, bypassing `schedule`/
      `report`) against the same 3 games from the real Week 1, 2026 slate, using the same
      `glm-5.3-flash:cloud` model as Milestone A — bounded to 3 games (~48 real cloud LLM calls
      total across all 5 strategies) to limit cost, and skipped `report`'s persistence
      deliberately so these test picks don't become the `runs` row `pickem score` would later
      grade for that week. All 5 produced a valid, correctly-ranked 3-pick set with zero
      validation errors; `llm_cache` grew by 61 rows, confirming per-role caching works
      independently across designs.

## Milestone D — Backtesting Harness

Goal: `pickem backtest` can replay historical seasons through any subset of the grid.

- [x] `backtest/cache.py` — repurposed from its original description. The LLM-response cache
      keyed on (game, context_hash, agent_design, prompt_variant, model, agent_role) already
      existed as `llm.py`'s `cache_key`/`predict` since Milestone A, and DESIGN §4 already
      specifies it's shared verbatim between live and backtest — decision #1 (backtest reuses
      the exact live graph) means the strategies backtest invokes call that same `llm.predict`
      directly, so there was nothing left to wrap. What the harness actually needed and didn't
      have: grid-cell-level idempotency. `insert_run`/`insert_picks` are plain INSERTs with no
      "already did this" check, so a resumed/re-run sweep would duplicate `runs` rows for a grid
      cell already completed and double-count it in `season_summary`. `already_run(conn, season,
      week, config)` is that check, backed by a new `repository.get_backtest_run` query. Verified
      in `tests/pickem/test_harness.py` by running the same tiny grid twice and confirming the
      second pass completes zero new runs and row counts don't change.
- [x] `backtest/harness.py` — walk-forward loop (DESIGN §9.1): for each requested season, each
      week 1–18, for each compatible config in the grid, invoke the exact compiled live graph
      (`graph.py`'s `build_graph`, unmodified) with `run_type="backtest"` and that config, then
      grade the returned picks against that week's already-final results and upsert `scores`.
      A week not yet `week_sync.synced_final` (future/in-progress) is skipped, not treated as an
      error, since a walk-forward replay can only score against fully-known historical labels.
      Required threading a `run_type` field through `PickemState` (`state.py`) and
      `nodes/reporting.py` (previously hardcoded `run_type="live"`) so the shared graph persists
      to the correct `runs` row for whichever kind of run is actually happening — the only change
      to the live path itself, and purely a parameterization, not a second code path.
      `scoring.py`'s per-game grading step was factored out into a public `grade_picks` so the
      harness and live `score` share the exact same correct/points_earned logic instead of a
      second implementation of it.
- [x] **Point-in-time correctness**: already true by construction — `nodes/context.py`'s record/
      recent-form/EPA/head-to-head builders all filter strictly on `week < target_week` (Milestone
      A/C). Added the dedicated regression test this bullet asked for anyway:
      `tests/pickem/test_point_in_time.py` seeds a two-week fixture where week 2's result is
      already sitting in the DB (as it would be for any real historical replay) and asserts week
      1's context is blind to it, and that week 2's own context reflects week 1 only — not itself.
- [x] Incompatible-pair handling: `harness._partition_grid` filters the grid into
      compatible/incompatible **before** any graph invocation, logging one warning per
      incompatible cell — so an incompatible pair is never even submitted to
      `nodes/prediction.py`'s hard-error check (that check still exists, unchanged, and still
      fires for `recommend`). Verified via `caplog` in `tests/pickem/test_harness.py`.
- [x] `pickem backtest --seasons ... --agent-design ... --prompt-variant ... --model ...
      --context-level ...` CLI wiring. All five flags are required (no defaults at all, not just
      `--seasons`) — a full cross-product is called out in DESIGN §3.3 as impractical, so there's
      no sensible "everything" default for any of them, matching the "always specify a targeted
      subset" framing. `--seasons` accepts `2022-2024` ranges and/or comma lists
      (`harness.parse_seasons`); the other four are plain comma lists (`harness.parse_list`).
- [x] Smoke test: `tests/pickem/test_harness.py` — a 2-week, 2-strategy, 1-model, `minimal`-tier
      grid (4 cells, 2 of them a deliberately incompatible pairing) runs end to end through the
      real graph/strategies/prompts/context/ranking/validation/persistence/scoring stack, with
      only `llm.predict` faked out (never talks to a real Ollama server, mirrors how
      `test_scoring.py` stubs `nflverse_client`) — confirms 4 `runs` rows and 8 `scores` rows,
      exactly as the grid math predicts, plus a `season_summary` sanity check.

## Milestone E — Evaluation & Reporting

Goal: `pickem backtest` output answers "which design would have won which season" with a report,
not just raw rows.

- [x] `season_summary` SQL view (DESIGN §9.2) — confirm it ranks configs correctly within a season
      against a hand-checked small fixture. The view itself already existed (Milestone A's
      `schema.sql`); added `tests/pickem/test_season_summary.py`, which seeds `runs`/`scores` rows
      directly (no graph) with two configs deliberately tied at 15 season points and a third at 8,
      and asserts `RANK()` places both ties at `season_rank=1` and skips straight to `3` — plus a
      second test confirming a `live` run's score never leaks into a `backtest`-only view.
- [x] Cross-season aggregation query/module (DESIGN §9.3): mean season points, standard deviation,
      seasons-won count, margin vs. naive-favorite baseline and market baseline. New
      `aggregation.py` (`aggregate_configs`) plus three new read-only `db/repository.py` queries
      (`season_summary_rows`, `backtest_weekly_points`, `backtest_calibration_rows`,
      `live_weekly_points`) — kept in `repository.py` per the existing "all SQL lives here"
      convention rather than scattering raw queries into `aggregation.py`/`viz/`. A baseline design
      run at more than one context level/model in the same season (e.g. `market_favorite` swept at
      both `standard` and `rich`) is averaged into one per-season baseline value before computing
      margins, so a wider baseline sweep can't silently outweigh a narrower one.
      `tests/pickem/test_aggregation.py` hand-checks mean/stdev/seasons-won/margins against a small
      fixture, including that multi-row-per-season averaging case.
- [x] Naive-favorite baseline generator (always pick the recorded favorite) runnable through the
      same walk-forward harness so it appears in the same `season_summary` rows as real configs.
      New `strategies/baseline.py` registers **both** `naive_favorite` (record + point-differential
      comparison, DESIGN §9.3/PRD §14) and `market_favorite` (de-vigged nflverse moneylines, spread
      as fallback — the market baseline §9.3 also calls for); both declare `required_roles = ()` so
      they're compatible with any `prompt_variant` and never call an LLM. New `prompts/baseline_v1.py`
      (empty templates) gives them a self-describing `prompt_variant` rather than borrowing an
      unrelated real prompt set's id; pass `--model none` since the field is unused. `market_favorite`
      degrades to a home-field default pick at `context_level` below `rich` (its feature slice only
      exists there) — same "legitimate grid cell, not a bug" precedent Milestone C's specialists set.
      Unit-tested directly in `tests/pickem/test_baseline_strategies.py`. Manually smoke-tested by
      running both baselines across the full real 2025 season (already fully synced, no network/LLM
      calls needed): `naive_favorite` scored identically at `minimal`/`rich` (1453 pts, confirming
      it's tier-independent as designed) and its picks visibly sharpen from 50/50 in week 1 (no
      record yet) to a real spread by week 18; `market_favorite` scored 1577 pts at `rich` vs. 1199
      at `minimal` (home-field-only), confirming the degrade path is real and costs real accuracy.
- [x] `viz/charts.py`: leaderboard table, per-season trend small-multiples, calibration/reliability
      chart, baseline comparison chart (DESIGN §9.4). Leaderboard is a plain HTML `<table>` (not a
      rendered image — the numbers stay selectable); the other three are matplotlib, returned as
      inline `<svg>` fragments (XML preamble stripped) rather than files, and share one Okabe-Ito
      colorblind-safe palette and axis style (`_style_axes`) as this project's first-ever chart code.
      Visually spot-checked by rendering each to PNG against the real 2025 season data: the
      calibration chart correctly shows `market_favorite`/`rich` tracking the y=x reliability line
      closely (well-calibrated real odds) while the season-trend chart's cumulative lines exactly
      match the leaderboard's point totals.
- [x] Single self-contained `report.html` generator bundling all of the above (embedded
      images/SVG, not linked files) under `reports/backtest/<batch_id>/` (DESIGN §9.4, §10
      decision #6). New `viz/report.py` (`generate_backtest_report`) + `pickem report --seasons`
      CLI command. `batch_id` defaults to a UTC timestamp; an empty result set (no backtest runs on
      file for the requested seasons) writes a plain "no data" page instead of erroring, since
      `report` is meant to be run independently of `backtest` against whatever's already persisted.
      `tests/pickem/test_report.py` asserts exactly 3 embedded `<svg>` blocks and zero `src=`
      attributes (nothing linked out); manually confirmed against the real 2025 season data too —
      the generated leaderboard's mean-points/margin numbers match the aggregation module's own
      hand-verified arithmetic exactly.
- [x] `recommend`'s own weekly charts (calibration scatter, season cumulative-points line) — the
      live-path equivalent of the backtest charts, deferred from Milestone A (DESIGN §8). Hooked
      into `nodes/reporting.py`'s existing persistence step: for a `run_type="live"` run only (a
      `backtest` sweep runs thousands of grid cells — a chart per cell would be unusable, and DESIGN
      §8's per-cell backtest comparison is already covered by Milestone E's batch `report.html`),
      writes `calibration_scatter.svg` + `season_cumulative_points.svg` under
      `reports/<season>/<week>/`. The cumulative chart degrades to a "no scored weeks yet" message
      rather than an empty/misleading line when `recommend` runs before any `pickem score` this
      season. `tests/pickem/test_live_reports.py` confirms both files are written for a live run and
      that a backtest run writes no `reports/` directory at all.

## Milestone F — Promotion & Hardening

Goal: go from "the harness works" to "a specific config is trusted for the live season."

- [x] Run the harness across the intended historical seasons with a targeted grid subset (DESIGN
      §3.3 notes the full cross-product is expected to be impractical at first — see PRD §14).
      Scope chosen with the user given real cost/time constraints: only `:cloud`-routed Ollama
      models exist on this machine (no true local model pulled) and the full 3-season × 5-design ×
      3-tier grid was ~41k sequential LLM calls at the measured ~1.7s/call — impractical to run in
      one pass. Ran **2025 only, all 5 designs, `standard` context tier, `glm-5.3-flash:cloud`**
      (~4,300 calls, ~3 hours), plus `naive_favorite`/`market_favorite` baselines (zero LLM cost).
      Found and fixed a real bug this run surfaced: `with_structured_output` can return `None` on a
      parse failure instead of raising, which the new retry wrapper didn't catch, crashing 3 of 90
      grid cells with `AttributeError: 'NoneType' object has no attribute 'model_dump'`. Added
      `llm.StructuredOutputParseError` (raised when the result is `None`, caught as retryable like
      throttling/network errors) plus regression tests (`tests/pickem/test_llm_retry.py`); re-ran
      and all 3 previously-failed cells completed cleanly. Also added throttling resilience per user
      request: `llm.py`'s `_invoke_with_retry` (exponential backoff + full jitter, 6 retries, on
      HTTP 429/5xx/connection errors) and harness-level fault tolerance (`backtest/harness.py`
      catches a per-cell exception, logs it, and continues rather than aborting the whole sweep —
      `runs_failed` in `BacktestSummary`/CLI output; a failed cell has no `runs` row, so re-invoking
      the same `pickem backtest` command picks it back up via existing grid-cell idempotency,
      without redoing any already-cached LLM call or already-completed cell).
      **Result** (`reports/backtest/20260906T170722Z/report.html`, sent to the user): all 5 designs
      beat `naive_favorite` (1453 pts) — `debate_advocate` best at 1526 (+73), then
      `ensemble_self_consistency` 1496, `single_analyst` 1480, `specialist_synthesis` 1479,
      `specialist_deterministic` 1473. **None beat the real `market_favorite` baseline** (1577 pts,
      run at `rich` tier — the only tier where it isn't a degenerate home-field-only default; a
      first attempt at averaging a `standard`-tier `market_favorite` run into the baseline was
      corrected and removed since that tier's value isn't real market signal and was diluting the
      comparison). Per DESIGN §9.3's own stated bar ("can't beat the market baseline... isn't a
      promotion candidate"), no design cleared it at `standard` tier.
      **Follow-up at `rich` tier** (user's request, after discussing where the market baseline data
      comes from and noting the `market` feature slice — and therefore every design's access to it —
      only exists at `rich`, DESIGN §3.3/§6): re-ran all 5 designs at `context_level=rich`, same
      season/model. Surfaced two more real bugs the same way as before — a malformed function-call
      response can also raise `pydantic.ValidationError` (observed: model omitted the required
      `rationale` field) rather than returning `None`, going unretried for the same reason;
      `_is_retryable` now also catches `ValidationError`, with a regression test. Result
      (`reports/backtest/20260907T121314Z/report.html`, sent to the user): **every design now beats
      the real market baseline** — `single_analyst` 1602 (market +25, naive +149),
      `specialist_deterministic` 1590 (+13/+137), `debate_advocate` 1588 (+11/+135),
      `specialist_synthesis` 1587 (+10/+134), `ensemble_self_consistency` 1586 (+9/+133), vs.
      `market_favorite` 1577 and `naive_favorite` 1453. The margins over market are small (9-25 pts
      out of ~1600, all 5 designs within a 16-point band of each other) and this is still a single
      season — DESIGN §9.3 explicitly warns against promoting on one season alone — so this reads as
      "the whole approach clears the bar once it can see market data" rather than "`single_analyst`
      is decisively the best design."
- [x] Review `report.html`; select and hand-set the production config in `config.toml` (DESIGN §5
      — promotion is manual, never automatic). Given how tight the single-season `rich`-tier margins
      were, the user asked to widen to more seasons on `single_analyst` specifically before deciding
      (rather than resweep all 5 designs across seasons — the cost of doing that for all 5 would
      have been the original ~41k-call problem again). Ran `single_analyst`/`rich` (+ baselines) for
      2024 then 2023 (`reports/backtest/20260907T124810Z/report.html`,
      `reports/backtest/20260907T132423Z/report.html`, both sent to the user). One real mistake made
      and caught mid-run: the first 2024 invocation crossed both `--prompt-variant` and `--model`
      lists, which — since baseline strategies' `required_roles=()` trivially satisfies any prompt
      set, and role-compatibility never checks `model` — silently created a `single_analyst`+
      `model=none` grid cell (would have wasted ~30-45 min retrying against a nonexistent model)
      plus 8 duplicate baseline rows differing only in irrelevant `model`/`prompt_variant` labels;
      caught from the log tail, process killed, junk rows deleted (kept the one good week already
      computed), re-ran with a correctly single-value grid per invocation.
      **3-season result (2023-2025, `single_analyst`/`rich`)**: beat `naive_favorite` in every
      season (mean margin +181 pts, low relative variance) but did *not* reliably beat
      `market_favorite` — mean margin only +3.7 pts across 3 seasons (stdev ~70-72 pts on both
      sides), losing outright in 2 of 3 seasons (2023: 1531 vs 1538; 2024: 1672 vs 1679) and winning
      only 2025 (1602 vs 1577). Beating real market odds turned out to be a much harder bar than
      DESIGN §9.3 anticipated pre-implementation.
      **Decision** (user, given this evidence): change the promotion bar from "must beat market" to
      "must beat naive (met); market is a stretch target, not a hard gate" — documented as DESIGN
      §10 decision #8, with §9.3 and PRD §14 updated to match. Promoted **`single_analyst` /
      `single_analyst_v1` / `glm-5.3-flash:cloud` / `rich`** to `config.toml` — chosen over the
      other 4 designs as the cheapest (1 LLM call/game) with no design showing a large enough edge
      in their one season of `rich`-tier data (Milestone F's first bullet, above) to justify the
      added call cost. Still a `:cloud`-routed model, not a true local one (unchanged caveat from
      Milestone A) — `config.toml` documents this and recommends re-backtesting before trusting a
      future local-model swap, since this promotion's evidence is specific to this model.
- [x] Unit tests: ranking algorithm (expected-points-maximizing permutation), validator edge cases
      (missing game, duplicate confidence, out-of-range probability), `current_week` boundary
      cases (pre-season, mid-week, post-Monday-night rollover). New `tests/pickem/test_ranking.py`
      (includes a brute-force check over all N! confidence assignments for a small N, confirming
      the sort-by-probability-descending assignment is never beaten by any permutation — the
      rearrangement-inequality claim, checked rather than assumed) and
      `tests/pickem/test_validator.py` (all listed edge cases plus all four `route_after_validate`
      branches). Extended `tests/pickem/test_dates.py` with `current_week` boundary cases: empty
      schedule (raises), pre-season, mid-week, post-Monday-night rollover, past-entire-schedule
      fallback, naive-datetime coercion to UTC, and the exact-kickoff `>=` boundary.
- [x] Confirm `score` and `recommend` share zero code paths that could cause cross-contamination
      (DESIGN §5.2's "the two stay cleanly separated" claim — worth a test, not just a comment).
      New `tests/pickem/test_code_separation.py`: spies on the real `repository` write functions
      and asserts `scoring.score_week` never calls `insert_run`/`insert_picks`, and the reporting
      node never calls `upsert_score`. All 52 pickem tests pass (`python -m pytest tests/pickem/`).
- [ ] First full live dry run: `pickem recommend` for a real week with the promoted config, manual
      transcription into Yahoo, `pickem score` the following week. First two steps done: ran
      `pickem recommend --season 2026 --week 1` with the promoted `single_analyst`/`rich` config
      (16/16 games, valid confidence permutation, persisted as a `live` run), and the user has
      transcribed those picks into Yahoo. Last step — `pickem score` — is blocked on the calendar,
      not on us: week 1 kicks off 2026-09-09 and isn't final until Monday Night Football wraps
      (~2026-09-15). Nothing left to do here until then; run `pickem score` once the week is over
      (it resolves the right week by default) to close this out.

## Milestone G — Score Prediction & Weekly Extremes

Goal: every `recommend` run also predicts a final score for each team in every game, and names the
single team predicted to score highest and the single team predicted to score lowest across that
week's whole slate (PRD FR9, DESIGN §10 decision #7). Purely additive — FR1's confidence ranking
and FR5's point scoring are untouched, since Yahoo grades on correct-winner + confidence rank only,
never margin.

**Scope note (depends on Milestone F):** this only needs to be built for the single `agent_design`/
`prompt_variant` combo Milestone F actually promotes to `config.toml` — not all 5 registered
strategies or the 2 baselines. FR9 doesn't feed backtest grading, so there's no evaluation reason
to compute predicted scores for designs that never run live; building combination logic for all 7
registry entries before F has even named a winner would mean throwing away most of that work.
`predicted_home_score`/`predicted_away_score` are therefore optional (`NotRequired[float]`) fields
in `AgentPrediction`/`Pick` — only the promoted strategy is required to populate them, the rest can
leave them unset. **This is why G is sequenced after F, not before.**

- [ ] `state.py`: add `predicted_home_score`/`predicted_away_score` as `NotRequired[float]` on
      `AgentPrediction` and `Pick` (DESIGN §3.1) — optional so unrelated strategies aren't forced to
      implement them.
- [ ] `db/schema.sql`: add nullable `predicted_home_score`/`predicted_away_score` columns to
      `picks`; runtime migration for existing `pickem.db` files, same `PRAGMA table_info` pattern
      as Milestone C's `games` migration (`repository._migrate_games_table`) — needed since sqlite3
      has no `ADD COLUMN IF NOT EXISTS`.
- [ ] Extend only the promoted strategy's prompt template(s) and structured-output schema to
      request `predicted_home_score`/`predicted_away_score` alongside the existing winner/
      probability/rationale fields, plus that strategy's own combination logic for the new fields
      (mirroring however it already combines `win_probability` — pass-through, weighted average,
      synthesis/judge call, whichever applies to the actual promoted design). The other 4
      strategies and 2 baselines are left untouched; add their score logic later only if a future
      backtest is specifically re-scoped to evaluate score-prediction quality.
- [ ] `nodes/reporting.py`: add predicted-score columns to the CLI table; print the week's single
      highest-predicted-score team and single lowest-predicted-score team across all games (this is
      a week-wide extremum over individual team scores, not a per-game high/low — the per-game
      favorite is already implied by the winner pick). Decide and document a tie-breaking rule
      (e.g. earliest kickoff wins ties). Since only the live promoted config populates the score
      fields, this only needs to handle the `run_type="live"` path, not backtest sweeps.
- [ ] Unit tests: schema migration and the weekly highest/lowest-score selection including the
      tie-break rule, exercised against the promoted strategy's combination logic.
- [ ] Manual smoke test: run `recommend` for a real week, confirm predicted scores print per game
      and the weekly high/low summary matches a hand-check of the printed per-game scores.
