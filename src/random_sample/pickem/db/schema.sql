-- Schema for the Yahoo Pro Pick'em Recommendation Agent.
-- See specs/pickem-agent/DESIGN.md §4.

CREATE TABLE IF NOT EXISTS runs (
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

CREATE TABLE IF NOT EXISTS games (
    game_id         TEXT PRIMARY KEY,
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    home_team       TEXT NOT NULL,
    away_team       TEXT NOT NULL,
    kickoff_ts      TEXT NOT NULL,
    home_score      INTEGER,
    away_score      INTEGER,
    home_rest       INTEGER,
    away_rest       INTEGER,
    div_game        INTEGER,
    roof            TEXT,
    surface         TEXT,
    spread_line     REAL,
    home_moneyline  INTEGER,
    away_moneyline  INTEGER,
    total_line      REAL
);

-- Pre-Milestone-C databases may already have a `games` table without the
-- situational/market columns above (sqlite3's ADD COLUMN has no IF NOT EXISTS form) —
-- see repository.get_connection's runtime migration step for those.

CREATE TABLE IF NOT EXISTS week_sync (
    season           INTEGER NOT NULL,
    week             INTEGER NOT NULL,
    synced_final     INTEGER NOT NULL DEFAULT 0,  -- 1 once every game in the week has a final score
    last_synced_at   TEXT NOT NULL,
    PRIMARY KEY (season, week)
);

CREATE TABLE IF NOT EXISTS picks (
    run_id             TEXT NOT NULL REFERENCES runs(run_id),
    game_id            TEXT NOT NULL REFERENCES games(game_id),
    predicted_winner   TEXT NOT NULL,
    win_probability    REAL NOT NULL,
    confidence         INTEGER NOT NULL,
    PRIMARY KEY (run_id, game_id)
);

CREATE TABLE IF NOT EXISTS scores (
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    game_id         TEXT NOT NULL REFERENCES games(game_id),
    correct         INTEGER NOT NULL,   -- 0/1
    points_earned   INTEGER NOT NULL,
    PRIMARY KEY (run_id, game_id)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key       TEXT PRIMARY KEY,   -- hash(game_id, context_hash, agent_design, prompt_variant, model, agent_role)
    game_id         TEXT NOT NULL,
    response_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS season_summary AS
SELECT season, agent_design, prompt_variant, model, context_level,
       SUM(points_earned) AS season_points,
       RANK() OVER (PARTITION BY season ORDER BY SUM(points_earned) DESC) AS season_rank
FROM scores JOIN runs USING (run_id)
WHERE runs.run_type = 'backtest'
GROUP BY season, agent_design, prompt_variant, model, context_level;
