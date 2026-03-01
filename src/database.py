"""
database.py
===========
All Supabase interactions for the PL predictor.

Tables used:
  fixtures    — every PL match (upcoming + completed) with xG actuals
  predictions — Monte Carlo outputs per fixture (one row per fixture)

The module exposes simple upsert / query helpers so that main.py stays clean.
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from supabase import create_client, Client

logger = logging.getLogger(__name__)


# ─── Client ──────────────────────────────────────────────────────────────────

def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def upsert_fixture(supabase: Client, fixture: Dict) -> Optional[int]:
    """
    Insert or update a fixture record.
    Unique key: (home_team, away_team, season)

    Business rules:
      • If the fixture is 'completed' in the DB, never overwrite with 'upcoming'.
      • If updating a 'completed' record (result coming in), update all fields.
      • If inserting/updating an 'upcoming' record, upsert freely.
    """
    try:
        # Check for an existing record
        existing = (
            supabase.table("fixtures")
            .select("id, status")
            .eq("home_team", fixture["home_team"])
            .eq("away_team", fixture["away_team"])
            .eq("season",    fixture["season"])
            .execute()
        )

        if existing.data:
            row = existing.data[0]
            fixture_id = row["id"]

            # Don't downgrade a completed result back to upcoming
            if row["status"] == "completed" and fixture.get("status") == "upcoming":
                return fixture_id

            supabase.table("fixtures").update(fixture).eq("id", fixture_id).execute()
            return fixture_id
        else:
            result = supabase.table("fixtures").insert(fixture).execute()
            if result.data:
                return result.data[0]["id"]
    except Exception as e:
        logger.error(f"upsert_fixture failed for "
                     f"{fixture.get('home_team')} vs {fixture.get('away_team')}: {e}")
    return None


def update_fixture_result(supabase: Client, fixture_id: int, result: Dict) -> bool:
    """Mark a fixture as completed and store actual goals + xG."""
    try:
        supabase.table("fixtures").update({
            "status":      "completed",
            "home_goals":  result.get("home_goals"),
            "away_goals":  result.get("away_goals"),
            "home_xg":     result.get("home_xg"),
            "away_xg":     result.get("away_xg"),
        }).eq("id", fixture_id).execute()
        return True
    except Exception as e:
        logger.error(f"update_fixture_result failed for fixture_id={fixture_id}: {e}")
        return False


def get_stale_upcoming_fixtures(supabase: Client) -> List[Dict]:
    """
    Return 'upcoming' fixtures whose match_date has passed > 2 hours ago.
    These need to be refreshed with actual results.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    try:
        result = (
            supabase.table("fixtures")
            .select("id, home_team, away_team, season")
            .eq("status", "upcoming")
            .lt("match_date", cutoff)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_stale_upcoming_fixtures failed: {e}")
        return []


# ─── Predictions ─────────────────────────────────────────────────────────────

def upsert_prediction(supabase: Client, fixture_id: int, prediction: Dict) -> bool:
    """
    Insert or update the Monte Carlo prediction for a fixture.
    One prediction row per fixture (unique on fixture_id).
    """
    try:
        existing = (
            supabase.table("predictions")
            .select("id")
            .eq("fixture_id", fixture_id)
            .execute()
        )

        payload = {**prediction, "fixture_id": fixture_id}

        if existing.data:
            pred_id = existing.data[0]["id"]
            supabase.table("predictions").update(payload).eq("id", pred_id).execute()
        else:
            supabase.table("predictions").insert(payload).execute()
        return True
    except Exception as e:
        logger.error(f"upsert_prediction failed for fixture_id={fixture_id}: {e}")
        return False


# ─── Read helpers (used by dashboard via Supabase REST, not Python) ───────────
# The dashboard queries Supabase directly via the REST API using the anon key.
# These helpers are provided for ad-hoc Python queries / debugging.

def get_upcoming_with_predictions(supabase: Client) -> List[Dict]:
    """Return all upcoming fixtures joined with their predictions."""
    try:
        result = (
            supabase.table("fixtures")
            .select("*, predictions(*)")
            .eq("status", "upcoming")
            .order("match_date", desc=False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_upcoming_with_predictions failed: {e}")
        return []


def get_completed_with_predictions(supabase: Client, limit: int = 50) -> List[Dict]:
    """Return recent completed fixtures joined with their predictions."""
    try:
        result = (
            supabase.table("fixtures")
            .select("*, predictions(*)")
            .eq("status", "completed")
            .order("match_date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_completed_with_predictions failed: {e}")
        return []
