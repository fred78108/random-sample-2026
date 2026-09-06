# PRD: Yahoo Pro Pick'em Recommendation Agent

Status: Draft v0.3 — no blocking open questions remain; a few implementation-level refinements are noted in §13.

## 1. Problem Statement

Each week during the NFL season, a model (designed separately, see project README) produces
predictions about game outcomes. Yahoo Pro Pick'em (confidence pool format) requires converting
those predictions into a specific, valid submission: pick a winner for every game on that week's
slate, and assign each pick a unique confidence value from 1 to N (N = number of games that week),
where higher values should go to the picks you're most confident in and scoring awards the
confidence value when the pick is correct.

Doing this by hand every week — figuring out the real slate (including byes and Thursday/Monday
games), converting probabilities into a valid 1..N ranking, and later checking how it scored — is
repetitive and error-prone. This agent automates that conversion and the scoring feedback loop.

## 2. Goals (v1)

- Given the model's win-probability output for a given week, compute a valid confidence
  assignment (a permutation of 1..N over that week's games) that maximizes expected points.
- Determine the correct set of games for a given week from real schedule data (nflverse), so the
  pick list always matches what Yahoo will actually list — no manual slate lookup.
- Provide a CLI that pulls the week's games and prepares a well-formatted, human-readable output
  for manual transcription into Yahoo.
- After a week's games complete, record actual results and compute points earned per pick and
  cumulative season points, so model performance can be tracked over the season.
- Produce visual reports (charts), not just tables, for weekly and season-level performance.
- Treat multi-agent design choices (e.g. how granular the prediction step should be) as an
  empirical question: build a scripted backtesting harness that evaluates candidate designs
  against historical seasons and picks a winner with evidence, rather than by assumption.

## 3. Non-Goals (v1)

- No automated login or session handling with Yahoo.
- No automated submission of picks to Yahoo (no browser automation, no scraping of Yahoo's forms).
- No support for other pick'em formats (straight-up, survivor) — confidence pool (N-ranking) only.
- No gambling/betting-market advice framing — this is for a private/free pick'em pool.
- No scheduled/automatic runs — v1 is manually invoked via CLI each week.

## 4. Future Work (explicitly deferred, not v1)

- Auto-submit picks directly to Yahoo (e.g. via browser automation), since Yahoo Pick'em has no
  public API. Deferred because it adds credential/session handling, is brittle to UI changes, and
  raises ToS considerations that deserve their own review before being built.
- React to line/injury movement between recommendation time and lock time.
- Multi-league or multi-user support.
- Scheduled/automatic weekly runs (v1 is manual-only by design).

## 5. Users

- Primary and only user: the project owner, running this weekly through the 2026 NFL season.

## 6. Inputs

- **Game characteristics (context)**: per-matchup features queried from nflverse (e.g. team
  records, recent form, EPA/play, rest days, head-to-head history, betting lines where available)
  — assembled by the Context Agent and passed into the LLM prompt. Exact feature set is a
  refinement task, not a blocker (see Open Questions).
- **LLM reasoning**: the Prediction Agent(s) call a locally-run LLM via
  [Ollama](https://ollama.com/) — no external API, no per-call cost, no API key, no data leaving
  the machine — to turn the assembled context into a win probability per game. Specific Ollama
  model(s) TBD; may itself be a variable the backtesting harness evaluates (see Open Questions).
- **Schedule data**: nflverse (via `nfl_data_py` / `nflreadpy`, already a project dependency) —
  teams, week number, kickoff datetime, bye teams. Also the source of historical seasons used by
  the backtesting harness.

## 7. Outputs

- **Weekly picks artifact**: CLI output — for the week's N games, the picked winner, assigned
  confidence value (1..N, descending = most confident), and the model's underlying win probability
  for that pick, sorted by confidence descending and formatted for easy manual transcription into
  Yahoo.
- **Results record**: after games complete, actual winner, hit/miss per pick, points earned, and
  running season total.
- **Visual reports**: charts covering weekly calibration (predicted probability vs. assigned
  confidence), season-to-date cumulative points/hit rate, and backtest comparisons across
  candidate agent designs.
- **SQLite database**: single local file holding games, picks, results, season totals, and
  backtest runs (see §8 FR4 and §10).

## 8. Functional Requirements

- **FR1 — Confidence assignment**: Given a week's win probabilities, assign confidence values as a
  permutation of 1..N such that expected points (sum of `points_if_correct(pick) * P(pick correct)`)
  is maximized. Confirmed: the league scores strictly by N-ranking (no upset bonus, no separate
  tiebreaker mechanic), so this reduces to ranking picks by predicted win probability of the chosen
  team, highest probability = highest confidence value.
- **FR2 — Slate & context resolution**: For a given week, pull the actual list of games from
  nflverse (excluding bye teams, matching what will appear on Yahoo), and assemble per-game
  characteristics (team records, recent form, EPA, rest days, head-to-head history, betting lines
  where available) to serve as context for the Prediction Agent's LLM call.
- **FR3 — CLI output**: A CLI command pulls the week's games and prepares a well-formatted,
  human-readable table (plus the visual reports in FR8) suitable for manual transcription into
  Yahoo. Manually invoked — no scheduling in v1.
- **FR4 — Persistence (SQLite)**: All state — resolved slates, recommended picks, actual results,
  season totals, and backtest runs — is stored in a local SQLite3 database (single file, no server
  dependency), so it's easy to update and query directly (including via plain SQL) as the season
  progresses.
- **FR5 — Scoring**: After a week's games are final, compute points earned per pick and cumulative
  season points; support a simple weekly/season summary report.
- **FR6 — Orchestration**: The weekly pipeline (FR1–FR3, FR5) is implemented as a LangGraph graph
  of distinct agent nodes with explicit state passed between them, not a single monolithic
  function — see Architecture below.
- **FR7 — Backtesting/evaluation harness**: A scripted, CLI-invokable harness replays historical
  seasons (via nflverse) through candidate configurations and scores each one with the same
  metrics as live scoring, so design decisions are made empirically rather than assumed. The
  harness varies (at least) three independent dimensions: agent granularity (§9), Ollama model
  choice, and context richness/data level. See §10.
- **FR8 — Visualization**: Generate charts alongside the CLI/table output — weekly calibration,
  season-to-date trend, and cross-design backtest comparisons (see §7, §10).

## 9. Architecture: Multi-Agent Design (LangGraph)

v1 is implemented as a graph of cooperating agents orchestrated with
[LangGraph](https://github.com/langchain-ai/langgraph), rather than a single linear script,
invoked through a CLI. Proposed nodes:

- **Schedule Agent** — resolves the week's actual game slate from nflverse (teams, kickoff times,
  bye teams).
- **Context Agent** — for each game on the resolved slate, queries nflverse for relevant
  characteristics (team records, recent form, EPA, rest days, head-to-head history, betting lines
  where available) and assembles them into structured context for the LLM prompt.
- **Prediction Agent(s)** — one or more LLM calls, via a locally-run Ollama model, prompted with
  the Context Agent's data, producing a win probability (and brief rationale) per game.
- **Confidence-Ranking Agent** — converts win probabilities into a valid 1..N confidence
  permutation optimizing expected points (FR1).
- **Validator Agent** — checks the proposed picks against the resolved slate (right number of
  games, valid 1..N permutation, matches expected Yahoo format) and routes back to
  Confidence-Ranking (or, if a probability itself is missing/malformed, back to Prediction) on
  failure rather than failing the whole run.
- **Reporting Agent** — formats the final picks for human transcription (FR3), writes to the
  SQLite database (FR4), and generates the visual reports (FR8). Once results are in, it also
  ingests actual outcomes to score the week and update the season-long record (FR5).

Control flow: `Schedule → Context → Prediction → Confidence-Ranking → Validator` with a
conditional edge that loops back on an invalid result, otherwise proceeding to `Reporting`. This
is a pipeline-with-validation-loop pattern rather than free-form agent debate. LangGraph's
explicit state graph, conditional edges, and retry/loop support map directly onto this control
flow.

**Agent granularity is not fixed by this spec.** Whether Prediction stays a single "analyst" LLM
agent given the full context, or decomposes into several specialist agents (e.g. stats, injuries,
betting-market) whose views get synthesized, is treated as a design variable — selected
empirically by the backtesting harness (§10) rather than assumed up front.

## 10. Evaluation & Backtesting Harness (AI Engineering Approach)

Rather than picking any of these variables by intuition, v1 includes a scripted, repeatable
backtesting harness that treats each as an axis to trial side-by-side:

**Experiment variables**

1. **Agent granularity** — single "analyst" Prediction Agent given full context vs. decomposed
   specialist agents (stats, injuries, betting-market) whose views get synthesized (§9).
2. **Model choice** — which Ollama model(s) power the Prediction Agent(s); multiple models are run
   head-to-head rather than one being picked upfront.
3. **Context richness** — different "levels" of data provided to the LLM, e.g. a minimal tier
   (basic records/standings) up through a rich tier (+ recent form, EPA, rest days, head-to-head,
   injuries, betting lines). Exact tier definitions are decided while building the Context Agent,
   not by this spec.

**Harness behavior**

- Replays historical seasons using nflverse's historical schedule/results data.
- Runs each combination of the variables above (a full cross-product, or a targeted subset if the
  grid gets too large — see Risks) over the same set of historical weeks.
- Scores each configuration with the same metric as live scoring (season points under N-ranking),
  plus supporting metrics useful for diagnosis (e.g. pick accuracy, probability calibration).
- **Caches LLM responses** in SQLite, keyed by (game, assembled-context hash, agent granularity,
  model, context level), so re-running the harness reuses prior local-inference results instead of
  re-running the LLM. Local inference has no per-call dollar cost, but replaying many historical
  weeks across a multi-variable grid still costs wall-clock time — caching is what keeps repeated
  runs cheap.
- Writes results to the same SQLite database as live data, in a `backtest_runs` table with columns
  for each variable (agent granularity, model, context level, season/week) so results can be
  sliced along any dimension, distinct from the live `picks`/`results` tables.
- Is invocable via the CLI, parameterized along each axis independently, e.g.
  `pickem backtest --seasons 2022-2025 --agent-design default,decomposed --model llama3.1,qwen2.5 --context-level minimal,standard,rich`
  so re-running it after a change is a scripted, repeatable action, not a one-off notebook
  exercise.
- Produces the cross-design comparison visuals called out in FR8, so a configuration is promoted
  to the live 2026 pipeline with evidence.

This is a hypothesis → offline evaluation → promote-to-production workflow applied to the agent
architecture itself, treating it as a tunable component the same way the underlying model would be
evaluated.

## 11. Non-Functional Requirements

- Runs standalone as a CLI — no dependency on a Yahoo session, no server component.
- Manually invoked; no scheduling built into v1 (see Non-Goals). Must be runnable with enough lead
  time before Thursday night kickoff each week (the earliest lock of the week).
- Persistence is a single local SQLite3 file — no external DB server, easy to inspect/query
  directly, consistent with the project's "no paid dependency" approach.
- No paid API dependency for v1: nflverse is free/no-key, and LLM inference runs locally via
  [Ollama](https://ollama.com/) — no external API key, no per-call cost, no data leaves the
  machine. Requires Ollama installed and running locally with the chosen model(s) pulled ahead of
  time.
- New dependencies introduced by this design: LangGraph (orchestration), an Ollama client (e.g.
  `langchain-ollama`) for LLM calls, and a charting library (e.g. matplotlib/plotly) for FR8.
- Local inference means latency/throughput depend on the host machine's hardware — a handful of
  calls for a live week is trivial, but the backtesting harness (§10) replaying many historical
  weeks × many designs could take a while; caching (§10) is what keeps repeated runs cheap.

## 12. Success Metrics

- Season-long cumulative confidence-pool points vs. league standings (if visible) or vs. a naive
  baseline (e.g. always pick the favorite by win probability with no optimization — to isolate
  whether the confidence-ranking logic itself adds value).
- Every weekly output is a *valid* submission: correct number of games, valid 1..N permutation,
  matches the actual Yahoo slate.
- Backtesting harness clearly discriminates between candidate agent designs (i.e. produces a
  usable, evidence-backed recommendation on agent granularity before the season needs it locked).
- Low manual effort per week beyond the act of typing picks into Yahoo's UI.

## 13. Open Questions

None remaining. Exact context-tier definitions and which specific Ollama models to trial are
implementation details to fill in while building the harness (§10), not spec-blocking decisions.

### Resolved

- ~~Yahoo scoring specifics~~ — league uses N-ranking only; no upset bonus or separate tiebreaker
  mechanic. FR1 stands as specified.
- ~~Persistence~~ — SQLite3 (FR4).
- ~~Interface~~ — CLI (FR3).
- ~~Cadence~~ — manual only, no scheduling (Non-Goals).
- ~~Agent granularity~~ — not fixed by fiat; resolved empirically via the backtesting harness
  (FR7, §10) before being locked in for the live season.
- ~~Model interface~~ — no separate trained model artifact; a Context Agent assembles per-matchup
  characteristics from nflverse, and a Prediction Agent reasons over that context via a
  locally-run LLM. See §9.
- ~~LLM provider~~ — Ollama, running locally. No external API key, no per-call cost, no data
  leaves the machine.
- ~~Ollama model choice~~ — confirmed as a backtesting variable: multiple models are trialed
  head-to-head rather than one being fixed upfront (§10).
- ~~Context feature set~~ — confirmed as a backtesting variable: multiple context-richness levels
  are trialed side-by-side rather than one being fixed upfront (§10).

## 14. Out-of-Scope Risks Worth Naming Now

- Yahoo has no public Pick'em API; anything beyond "read public schedule data and produce a
  recommendation" (i.e. Phase 2 auto-submit) would need its own ToS/risk review before being built.
- **Local LLM quality**: a locally-run Ollama model may reason less reliably than a frontier
  hosted model. The backtesting harness (§10) should confirm LLM-based picks actually beat a naive
  statistical baseline (e.g. always favor the team with the better record) before the design is
  trusted for live picks.
- **Backtest runtime / combinatorial grid**: the three experiment variables (agent granularity ×
  model × context level, §10) multiply together — and agent granularity alone now spans 5
  candidate orchestration patterns (see DESIGN.md §3.3/§9: single analyst, specialist+synthesis,
  specialist+deterministic, debate/advocate, self-consistency ensemble), some of which issue
  several LLM calls per game. A full cross-product over many historical weeks via local inference
  is expected to be impractical on a first pass, not just a risk to watch for. Caching mitigates
  *repeated* runs; running a targeted subset of the grid — not the full cross-product — is the
  expected default, widened once the harness and cache are proven out.
