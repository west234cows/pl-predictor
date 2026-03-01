"""
main.py
=======
Orchestrates the full PL prediction pipeline:

  1. Fetch completed results + upcoming fixtures from Understat
  2. Reconcile completed results in Supabase (update any stale 'upcoming' rows)
  3. Calculate rolling xG-based team ratings
  4. Run Monte Carlo simulation for every upcoming fixture
  5. Store predictions in Supabase

Run locally:
  export SUPABASE_URL=https://xxxx.supabase.co
  export SUPABASE_KEY=your-anon-or-service-role-key
  python src/main.py

Or triggered automatically by GitHub Actions (.github/workflows/predictions.yml).
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

from scraper import (
    fetch_season_data,
    calculate_team_ratings,
    parse_completed_results,
    parse_upcoming_fixtures,
    CURRENT_SEASON,
)
from monte_carlo import run_simulation
from database import (
    get_client,
    upsert_fixture,
    upsert_prediction,
    get_stale_upcoming_fixtures,
    update_fixture_result,
)

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
# ─────────────────────────────────────────────────────────────────────────────


async def run():
    start = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info(f"PL Predictor — season {CURRENT_SEASON}/{CURRENT_SEASON + 1}")
    logger.info(f"Started at {start.strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)

    # ── 1. Fetch data from Understat ──────────────────────────────────────
    raw_results, raw_fixtures = await fetch_season_data()

    # ── 2. Parse into clean dicts ─────────────────────────────────────────
    completed = parse_completed_results(raw_results)
    upcoming  = parse_upcoming_fixtures(raw_fixtures)

    # ── 3. Connect to Supabase ────────────────────────────────────────────
    supabase = get_client()
    logger.info("Connected to Supabase ✓")

    # ── 4. Store / update completed results ──────────────────────────────
    logger.info(f"Upserting {len(completed)} completed results...")
    for match in completed:
        upsert_fixture(supabase, match)

    # ── 5. Reconcile any stale 'upcoming' rows that are now finished ──────
    #   (Handles the case where the script ran before the match but
    #    Understat now has the final result in 'raw_results')
    stale = get_stale_upcoming_fixtures(supabase)
    if stale:
        logger.info(f"Reconciling {len(stale)} stale upcoming fixtures...")
        # Build a lookup from completed results
        result_lookup = {
            (r["home_team"], r["away_team"]): r
            for r in completed
        }
        for row in stale:
            key = (row["home_team"], row["away_team"])
            if key in result_lookup:
                update_fixture_result(supabase, row["id"], result_lookup[key])
                logger.info(f"  ✓ Updated result: {key[0]} vs {key[1]}")

    # ── 6. Calculate team ratings ─────────────────────────────────────────
    logger.info("Calculating team ratings from xG data...")
    ratings, league_avg = calculate_team_ratings(raw_results)

    # ── 7. Run simulations for upcoming fixtures ──────────────────────────
    logger.info(f"Running Monte Carlo simulations for {len(upcoming)} upcoming fixtures...")
    predictions_stored = 0

    for fixture in upcoming:
        home = fixture["home_team"]
        away = fixture["away_team"]

        # Upsert the fixture record, get its DB id
        fixture_id = upsert_fixture(supabase, fixture)
        if fixture_id is None:
            logger.warning(f"  ⚠  Could not upsert fixture: {home} vs {away}")
            continue

        # Run simulation
        prediction = run_simulation(home, away, ratings, league_avg)

        # Store prediction
        if upsert_prediction(supabase, fixture_id, prediction):
            predictions_stored += 1
            logger.info(
                f"  ✓ {home:<25} vs {away:<25}  "
                f"H:{prediction['home_win_pct']:5.1f}%  "
                f"D:{prediction['draw_pct']:5.1f}%  "
                f"A:{prediction['away_win_pct']:5.1f}%"
            )
        else:
            logger.warning(f"  ⚠  Could not store prediction for: {home} vs {away}")

    # ── 8. Summary ────────────────────────────────────────────────────────
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info("=" * 60)
    logger.info(f"Done in {elapsed:.1f}s")
    logger.info(f"  Results stored   : {len(completed)}")
    logger.info(f"  Predictions made : {predictions_stored} / {len(upcoming)}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(run())
