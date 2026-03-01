"""
scraper.py
==========
Fetches Premier League data from Understat:
  - Completed match results with xG
  - Upcoming fixtures
  - Team attack/defence ratings derived from rolling xG averages

Season convention: Understat uses the *start* year of the season.
  2025/26 season → season=2025
"""

import asyncio
import aiohttp
from understat import Understat
from datetime import datetime, timezone
from typing import Dict, List, Tuple
import numpy as np
import logging

logger = logging.getLogger(__name__)

# ─── Config ─────────────────────────────────────────────────────────────────
CURRENT_SEASON = 2025          # 2025/26 season
LEAGUE = "EPL"
ROLLING_WINDOW = 10            # matches used for rolling rating calculation
MIN_MATCHES = 3                # minimum matches before using team's own rating

# Premier League baseline xG averages (fallback for early season / new teams)
LEAGUE_BASELINE = {
    "home_attack": 1.38,
    "home_defence": 1.10,
    "away_attack": 1.10,
    "away_defence": 1.38,
    "avg_home_xg": 1.38,
    "avg_away_xg": 1.10,
}
# ─────────────────────────────────────────────────────────────────────────────


async def fetch_season_data(season: int = CURRENT_SEASON) -> Tuple[List[Dict], List[Dict]]:
    """
    Fetch completed results and upcoming fixtures from Understat.
    Returns (results, fixtures).
    """
    async with aiohttp.ClientSession() as session:
        understat = Understat(session)
        logger.info(f"Fetching {LEAGUE} data for season {season}...")
        results = await understat.get_league_results(LEAGUE, season)
        fixtures = await understat.get_league_fixtures(LEAGUE, season)
        logger.info(f"  Fetched {len(results)} results, {len(fixtures)} fixtures")
        return results, fixtures


def calculate_team_ratings(
    results: List[Dict],
    n: int = ROLLING_WINDOW,
    min_matches: int = MIN_MATCHES,
) -> Tuple[Dict[str, Dict], Dict[str, float]]:
    """
    Calculate attack & defence ratings for each team using rolling xG averages.

    Model:
      home_xg_exp = home_attack * away_defence / league_avg_home
      away_xg_exp = away_attack * home_defence / league_avg_away

    Returns:
      ratings  : {team_name: {home_attack, home_defence, away_attack, away_defence}}
      league_avg: {home: float, away: float}
    """
    # Sort chronologically
    sorted_results = sorted(results, key=lambda x: x.get("datetime", ""))

    # Accumulate per-team xG history
    team_stats: Dict[str, Dict[str, List[float]]] = {}

    for match in sorted_results:
        try:
            home = match["h"]["title"]
            away = match["a"]["title"]
            home_xg = float(match["xG"]["h"])
            away_xg = float(match["xG"]["a"])
        except (KeyError, ValueError, TypeError):
            continue

        for team in (home, away):
            if team not in team_stats:
                team_stats[team] = {
                    "home_scored": [],
                    "home_conceded": [],
                    "away_scored": [],
                    "away_conceded": [],
                }

        team_stats[home]["home_scored"].append(home_xg)
        team_stats[home]["home_conceded"].append(away_xg)
        team_stats[away]["away_scored"].append(away_xg)
        team_stats[away]["away_conceded"].append(home_xg)

    # Compute rolling ratings
    ratings: Dict[str, Dict] = {}
    all_home_xg: List[float] = []
    all_away_xg: List[float] = []

    for team, stats in team_stats.items():
        # Take last N matches; fall back to baseline if too few games
        hs = stats["home_scored"][-n:] if len(stats["home_scored"]) >= min_matches else None
        hc = stats["home_conceded"][-n:] if len(stats["home_conceded"]) >= min_matches else None
        as_ = stats["away_scored"][-n:] if len(stats["away_scored"]) >= min_matches else None
        ac = stats["away_conceded"][-n:] if len(stats["away_conceded"]) >= min_matches else None

        ratings[team] = {
            "home_attack":   round(float(np.mean(hs)), 4) if hs else LEAGUE_BASELINE["home_attack"],
            "home_defence":  round(float(np.mean(hc)), 4) if hc else LEAGUE_BASELINE["home_defence"],
            "away_attack":   round(float(np.mean(as_)), 4) if as_ else LEAGUE_BASELINE["away_attack"],
            "away_defence":  round(float(np.mean(ac)), 4) if ac else LEAGUE_BASELINE["away_defence"],
            "home_matches":  len(stats["home_scored"]),
            "away_matches":  len(stats["away_scored"]),
        }

        if hs:
            all_home_xg.extend(hs)
        if as_:
            all_away_xg.extend(as_)

    league_avg = {
        "home": float(np.mean(all_home_xg)) if all_home_xg else LEAGUE_BASELINE["avg_home_xg"],
        "away": float(np.mean(all_away_xg)) if all_away_xg else LEAGUE_BASELINE["avg_away_xg"],
    }

    logger.info(f"Ratings calculated for {len(ratings)} teams. "
                f"League avg xG: home={league_avg['home']:.3f}, away={league_avg['away']:.3f}")
    return ratings, league_avg


def parse_completed_results(results: List[Dict]) -> List[Dict]:
    """
    Convert raw Understat results into clean dicts ready for Supabase upsert.
    """
    parsed = []
    for r in results:
        try:
            parsed.append({
                "home_team":   r["h"]["title"],
                "away_team":   r["a"]["title"],
                "match_date":  _normalise_dt(r["datetime"]),
                "home_goals":  int(r["goals"]["h"]),
                "away_goals":  int(r["goals"]["a"]),
                "home_xg":     round(float(r["xG"]["h"]), 3),
                "away_xg":     round(float(r["xG"]["a"]), 3),
                "season":      str(CURRENT_SEASON),
                "status":      "completed",
            })
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Skipping result due to parse error: {e}")
            continue
    return parsed


def parse_upcoming_fixtures(fixtures: List[Dict]) -> List[Dict]:
    """
    Filter to future fixtures only and return clean dicts.
    """
    now = datetime.now(timezone.utc)
    upcoming = []

    for f in fixtures:
        try:
            dt_str = _normalise_dt(f["datetime"])
            match_dt = datetime.fromisoformat(dt_str)
            if match_dt.tzinfo is None:
                match_dt = match_dt.replace(tzinfo=timezone.utc)

            if match_dt <= now:
                continue  # already in the past

            upcoming.append({
                "home_team":  f["h"]["title"],
                "away_team":  f["a"]["title"],
                "match_date": dt_str,
                "season":     str(CURRENT_SEASON),
                "status":     "upcoming",
            })
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Skipping fixture due to parse error: {e}")
            continue

    logger.info(f"Found {len(upcoming)} upcoming fixtures")
    return upcoming


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _normalise_dt(dt_str: str) -> str:
    """
    Understat datetimes are 'YYYY-MM-DD HH:MM:SS' in UTC.
    Convert to ISO-8601 with timezone for Supabase.
    """
    return dt_str.strip().replace(" ", "T") + "+00:00"
