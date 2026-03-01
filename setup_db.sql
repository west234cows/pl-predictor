-- ============================================================
-- PL Predictor — Supabase Database Setup
-- ============================================================
-- Run this in the Supabase SQL Editor (one paste, one click).
-- Dashboard: https://supabase.com/dashboard → your project → SQL Editor
-- ============================================================


-- ── fixtures ────────────────────────────────────────────────────────────────
-- Every PL match — both upcoming (no goals/xG) and completed (with actuals).
CREATE TABLE IF NOT EXISTS public.fixtures (
    id          BIGSERIAL PRIMARY KEY,
    home_team   TEXT        NOT NULL,
    away_team   TEXT        NOT NULL,
    match_date  TIMESTAMPTZ NOT NULL,
    season      TEXT        NOT NULL DEFAULT '2025',

    -- 'upcoming' until the match is played, then 'completed'
    status      TEXT        NOT NULL DEFAULT 'upcoming'
                CHECK (status IN ('upcoming', 'completed')),

    -- Actual result (NULL until completed)
    home_goals  SMALLINT,
    away_goals  SMALLINT,

    -- Actual xG from Understat (NULL until completed)
    home_xg     NUMERIC(5, 3),
    away_xg     NUMERIC(5, 3),

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Each team pair plays once at home per season
    CONSTRAINT uq_fixture UNIQUE (home_team, away_team, season)
);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS fixtures_updated_at ON public.fixtures;
CREATE TRIGGER fixtures_updated_at
    BEFORE UPDATE ON public.fixtures
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Indexes
CREATE INDEX IF NOT EXISTS idx_fixtures_match_date ON public.fixtures (match_date);
CREATE INDEX IF NOT EXISTS idx_fixtures_status     ON public.fixtures (status);
CREATE INDEX IF NOT EXISTS idx_fixtures_season     ON public.fixtures (season);


-- ── predictions ─────────────────────────────────────────────────────────────
-- Monte Carlo outputs — one row per fixture, overwritten each run.
CREATE TABLE IF NOT EXISTS public.predictions (
    id                    BIGSERIAL PRIMARY KEY,
    fixture_id            BIGINT      NOT NULL
                          REFERENCES public.fixtures (id) ON DELETE CASCADE,

    -- Probability outputs (sum to ~100)
    home_win_pct          NUMERIC(5, 2) NOT NULL,
    draw_pct              NUMERIC(5, 2) NOT NULL,
    away_win_pct          NUMERIC(5, 2) NOT NULL,

    -- Model's expected goals
    predicted_home_goals  NUMERIC(5, 3),
    predicted_away_goals  NUMERIC(5, 3),

    -- Metadata
    simulations           INTEGER     NOT NULL DEFAULT 100000,
    model_version         TEXT        NOT NULL DEFAULT 'v1.1-poisson-dc',

    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_prediction_per_fixture UNIQUE (fixture_id)
);

DROP TRIGGER IF EXISTS predictions_updated_at ON public.predictions;
CREATE TRIGGER predictions_updated_at
    BEFORE UPDATE ON public.predictions
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE INDEX IF NOT EXISTS idx_predictions_fixture_id
    ON public.predictions (fixture_id);


-- ── Convenience view (used by the dashboard) ────────────────────────────────
CREATE OR REPLACE VIEW public.vw_upcoming_predictions AS
SELECT
    f.id,
    f.home_team,
    f.away_team,
    f.match_date,
    f.season,
    f.status,
    p.home_win_pct,
    p.draw_pct,
    p.away_win_pct,
    p.predicted_home_goals,
    p.predicted_away_goals,
    p.model_version,
    p.updated_at AS predicted_at
FROM  public.fixtures    f
JOIN  public.predictions p ON f.id = p.fixture_id
WHERE f.status = 'upcoming'
ORDER BY f.match_date ASC;


CREATE OR REPLACE VIEW public.vw_results_vs_predictions AS
SELECT
    f.id,
    f.home_team,
    f.away_team,
    f.match_date,
    f.season,
    f.home_goals,
    f.away_goals,
    f.home_xg       AS actual_home_xg,
    f.away_xg       AS actual_away_xg,
    p.home_win_pct,
    p.draw_pct,
    p.away_win_pct,
    p.predicted_home_goals,
    p.predicted_away_goals,
    -- Derive actual outcome
    CASE
        WHEN f.home_goals >  f.away_goals THEN 'H'
        WHEN f.home_goals =  f.away_goals THEN 'D'
        ELSE 'A'
    END AS actual_outcome,
    -- Derive predicted favourite
    CASE
        WHEN p.home_win_pct >= p.draw_pct AND p.home_win_pct >= p.away_win_pct THEN 'H'
        WHEN p.draw_pct     >= p.home_win_pct AND p.draw_pct >= p.away_win_pct THEN 'D'
        ELSE 'A'
    END AS predicted_favourite,
    p.updated_at AS predicted_at
FROM  public.fixtures    f
JOIN  public.predictions p ON f.id = p.fixture_id
WHERE f.status = 'completed'
ORDER BY f.match_date DESC;


-- ── Row Level Security ───────────────────────────────────────────────────────
-- Allow the dashboard (anonymous key) to SELECT.
-- The service-role key used by GitHub Actions bypasses RLS entirely.

ALTER TABLE public.fixtures    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;

-- Public read on fixtures
DROP POLICY IF EXISTS "anon_read_fixtures"    ON public.fixtures;
CREATE POLICY "anon_read_fixtures"
    ON public.fixtures FOR SELECT
    USING (true);

-- Public read on predictions
DROP POLICY IF EXISTS "anon_read_predictions" ON public.predictions;
CREATE POLICY "anon_read_predictions"
    ON public.predictions FOR SELECT
    USING (true);

-- Grant SELECT on views to the anon role
GRANT SELECT ON public.vw_upcoming_predictions    TO anon;
GRANT SELECT ON public.vw_results_vs_predictions  TO anon;


-- ── Done ─────────────────────────────────────────────────────────────────────
-- You should now see 'fixtures' and 'predictions' in your Table Editor.
-- ============================================================
