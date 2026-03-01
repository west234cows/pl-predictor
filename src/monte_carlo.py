"""
monte_carlo.py
==============
Poisson-based Monte Carlo simulation for Premier League fixtures.

Model overview
--------------
Each team is characterised by rolling-average xG attack and defence ratings
(calculated by scraper.calculate_team_ratings).

For a fixture  Home vs Away:

  home_λ = home_attack  × away_defence  / league_avg_home  × home_advantage
  away_λ = away_attack  × home_defence  / league_avg_away

Goals are drawn independently from Poisson(λ), repeated N times.
The proportions of H / D / A outcomes give the probability estimates.

Dixon-Coles low-score correction is applied (ρ parameter) to fix the
slight over-estimation of 0-0 and under-estimation of 1-0/0-1 draws that
the independent Poisson model produces.
"""

import numpy as np
from typing import Dict
import logging

logger = logging.getLogger(__name__)

# ─── Config ─────────────────────────────────────────────────────────────────
N_SIMULATIONS   = 100_000
HOME_ADVANTAGE  = 1.10        # ~10% uplift for playing at home
DC_RHO          = -0.13       # Dixon-Coles low-score correction parameter
MODEL_VERSION   = "v1.1-poisson-dc"
# ─────────────────────────────────────────────────────────────────────────────


def _dc_correction(home_goals: np.ndarray, away_goals: np.ndarray, rho: float) -> np.ndarray:
    """
    Apply Dixon-Coles correction weight to simulated results.
    Adjusts the probability mass around (0,0), (1,0), (0,1), (1,1).

    Returns an array of multiplicative weights (most are 1.0).
    """
    tau = np.ones(len(home_goals), dtype=float)

    mask_00 = (home_goals == 0) & (away_goals == 0)
    mask_10 = (home_goals == 1) & (away_goals == 0)
    mask_01 = (home_goals == 0) & (away_goals == 1)
    mask_11 = (home_goals == 1) & (away_goals == 1)

    # These weight adjustments come from the original Dixon-Coles (1997) paper
    # τ(0,0) = 1 − μ_h × μ_a × ρ
    # τ(1,0) = 1 + μ_a × ρ   (stored as scalar; approximated here element-wise)
    # τ(0,1) = 1 + μ_h × ρ
    # τ(1,1) = 1 − ρ
    tau[mask_00] = 1.0 - rho   # simplified scalar form
    tau[mask_10] = 1.0 + rho
    tau[mask_01] = 1.0 + rho
    tau[mask_11] = 1.0 - rho

    return tau


def run_simulation(
    home_team: str,
    away_team: str,
    ratings: Dict[str, Dict],
    league_avg: Dict[str, float],
    n_simulations: int = N_SIMULATIONS,
    apply_dc_correction: bool = True,
) -> Dict:
    """
    Run a Monte Carlo simulation for a single fixture.

    Parameters
    ----------
    home_team / away_team : team name strings (must match keys in `ratings`)
    ratings               : output of scraper.calculate_team_ratings()
    league_avg            : {"home": float, "away": float}
    n_simulations         : number of random match samples
    apply_dc_correction   : whether to apply Dixon-Coles low-score correction

    Returns
    -------
    dict with keys:
      home_win_pct, draw_pct, away_win_pct  (floats, sum to 100)
      predicted_home_goals, predicted_away_goals
      simulations, model_version
    """
    home_r = ratings.get(home_team, {})
    away_r = ratings.get(away_team, {})

    avg_h = max(league_avg.get("home", 1.38), 0.01)
    avg_a = max(league_avg.get("away", 1.10), 0.01)

    # Expected goals (λ) for each side
    home_lambda = (
        home_r.get("home_attack",  avg_h)
        * away_r.get("away_defence", avg_h)
        / avg_h
        * HOME_ADVANTAGE
    )
    away_lambda = (
        away_r.get("away_attack",  avg_a)
        * home_r.get("home_defence", avg_a)
        / avg_a
    )

    # Guard against pathological values
    home_lambda = float(np.clip(home_lambda, 0.1, 8.0))
    away_lambda = float(np.clip(away_lambda, 0.1, 8.0))

    # ── Simulate ──────────────────────────────────────────────────────────
    rng = np.random.default_rng()
    home_goals = rng.poisson(home_lambda, n_simulations)
    away_goals = rng.poisson(away_lambda, n_simulations)

    if apply_dc_correction:
        weights = _dc_correction(home_goals, away_goals, DC_RHO)
        home_wins = float(np.sum(weights[(home_goals > away_goals)]))
        draws     = float(np.sum(weights[(home_goals == away_goals)]))
        away_wins = float(np.sum(weights[(away_goals > home_goals)]))
        total     = home_wins + draws + away_wins
    else:
        home_wins = float(np.sum(home_goals > away_goals))
        draws     = float(np.sum(home_goals == away_goals))
        away_wins = float(np.sum(away_goals > home_goals))
        total     = float(n_simulations)

    if total == 0:
        # Should never happen, but guard anyway
        home_win_pct = draw_pct = away_win_pct = round(100 / 3, 2)
    else:
        home_win_pct = round(home_wins / total * 100, 2)
        draw_pct     = round(draws     / total * 100, 2)
        away_win_pct = round(away_wins / total * 100, 2)

    result = {
        "home_win_pct":          home_win_pct,
        "draw_pct":              draw_pct,
        "away_win_pct":          away_win_pct,
        "predicted_home_goals":  round(home_lambda, 3),
        "predicted_away_goals":  round(away_lambda, 3),
        "simulations":           n_simulations,
        "model_version":         MODEL_VERSION,
    }

    logger.debug(
        f"{home_team} vs {away_team}: "
        f"λH={home_lambda:.2f} λA={away_lambda:.2f}  |  "
        f"H{home_win_pct}% D{draw_pct}% A{away_win_pct}%"
    )
    return result
