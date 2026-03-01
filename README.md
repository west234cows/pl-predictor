# ⚽ PL Predictor

End-to-end Premier League result prediction system:

- **Scrapes** xG data from [Understat](https://understat.com)
- **Simulates** 100,000 matches per fixture using a Poisson Monte Carlo model
- **Stores** predictions, fixtures and results in [Supabase](https://supabase.com) (Postgres)
- **Runs automatically** every week via GitHub Actions — no server needed
- **Displays** predictions on a mobile-friendly web dashboard you can bookmark on your phone

---

## 🗂 Project structure

```
pl-predictor/
├── src/
│   ├── scraper.py         # Fetch xG data from Understat + calculate team ratings
│   ├── monte_carlo.py     # Poisson Monte Carlo simulation (Dixon-Coles corrected)
│   ├── database.py        # Supabase upsert / query helpers
│   └── main.py            # Orchestrator — run this script
├── dashboard/
│   └── index.html         # Mobile-friendly web dashboard (host on GitHub Pages)
├── .github/
│   └── workflows/
│       └── predictions.yml  # GitHub Actions — runs Thu, Fri, Mon automatically
├── setup_db.sql           # Paste into Supabase SQL Editor to create tables
└── requirements.txt
```

---

## 🚀 Setup — step by step

### Step 1 — Fork / create the repository on GitHub

Push this folder to a new GitHub repository (public or private).

```bash
git init
git remote add origin https://github.com/YOUR_USERNAME/pl-predictor.git
git add .
git commit -m "Initial commit"
git push -u origin main
```

---

### Step 2 — Create a Supabase project

1. Go to [supabase.com](https://supabase.com) → **New project**
2. Choose a name (e.g. `pl-predictor`) and a strong DB password
3. Wait ~2 minutes for the project to provision
4. Open **SQL Editor** → paste the full contents of `setup_db.sql` → click **Run**
   - This creates the `fixtures` and `predictions` tables plus two dashboard views
5. Go to **Project Settings → API** and copy:
   - **Project URL** (looks like `https://xxxx.supabase.co`)
   - **anon public key** (the `eyJ…` JWT — safe to use in the frontend)
   - **service_role secret key** (for GitHub Actions only — keep this private)

---

### Step 3 — Add GitHub Secrets

In your GitHub repository go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name     | Value                                  |
|-----------------|----------------------------------------|
| `SUPABASE_URL`  | `https://xxxx.supabase.co`             |
| `SUPABASE_KEY`  | Your Supabase **service_role** key     |

> **Note:** The service_role key bypasses Row Level Security, so GitHub Actions can write to the database. The anon key is used in the dashboard (read-only via RLS policies that were created by `setup_db.sql`).

---

### Step 4 — Run the pipeline for the first time

Go to **GitHub → Actions → PL Predictions Pipeline → Run workflow → Run workflow**.

The pipeline will:
1. Scrape all 2025/26 season results from Understat
2. Calculate rolling xG ratings for all 20 teams
3. Run 100,000-simulation Monte Carlo for each upcoming fixture
4. Store everything in Supabase

---

### Step 5 — Deploy the dashboard (GitHub Pages)

1. In your GitHub repo go to **Settings → Pages**
2. Set **Source** to `Deploy from a branch`
3. Set **Branch** to `main` and **Folder** to `/dashboard`
4. Click **Save** — GitHub will give you a URL like `https://YOUR_USERNAME.github.io/pl-predictor/`
5. Bookmark that URL on your phone!

**First-time setup on phone:**
- Open the dashboard URL
- Tap the **⚙️ Setup** tab
- Enter your Supabase Project URL and **anon** key (NOT the service_role key)
- Tap **Save & Connect**

Your credentials are stored in your browser's local storage and never leave your device.

---

## 🔄 Automated schedule

The GitHub Actions workflow runs automatically:

| Day      | Time (UTC) | Purpose                                       |
|----------|------------|-----------------------------------------------|
| Thursday | 09:00      | Generate predictions before weekend fixtures  |
| Friday   | 09:00      | Top-up for Friday night game                  |
| Monday   | 09:00      | Reconcile weekend results, update database    |

You can also trigger it manually any time from the **Actions** tab in GitHub.

---

## 🧠 Model details

### Data source
[Understat](https://understat.com) provides post-shot expected goals (xG) for every Premier League match. The `understat` Python package is used for async data fetching.

### Team ratings
For each team, rolling **xG attack** and **xG defence** ratings are calculated from the last 10 home/away matches separately:

```
home_attack  = mean(xG scored in last 10 home games)
home_defence = mean(xG conceded in last 10 home games)
away_attack  = mean(xG scored in last 10 away games)
away_defence = mean(xG conceded in last 10 away games)
```

### Expected goals per fixture
```
home_λ = home_attack × away_defence / league_avg_home × 1.10 (home advantage)
away_λ = away_attack × home_defence / league_avg_away
```

### Monte Carlo simulation
100,000 match outcomes are simulated by drawing independently from:

```
home_goals ~ Poisson(home_λ)
away_goals ~ Poisson(away_λ)
```

**Dixon-Coles correction** (`ρ = −0.13`) is applied to adjust the probability mass around low-scoring outcomes (0-0, 1-0, 0-1, 1-1), which the independent Poisson model slightly mis-estimates.

The proportions of Home Win / Draw / Away Win across the 100,000 simulations are the final probability outputs.

### Accuracy metrics
The dashboard **Stats** tab shows:
- Overall outcome accuracy (did the highest-probability outcome happen?)
- Accuracy broken down by Home Win / Draw / Away Win
- **Brier score** — a proper scoring rule that rewards well-calibrated probabilities (lower = better; random = 0.667, perfect = 0.0)

---

## 🛠 Running locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export SUPABASE_URL=https://xxxx.supabase.co
export SUPABASE_KEY=your-service-role-key

# Run
python src/main.py
```

Requires Python 3.10+.

---

## 🔧 Configuration

Key constants you can tweak in `src/scraper.py`:

| Constant         | Default | Description                              |
|------------------|---------|------------------------------------------|
| `CURRENT_SEASON` | `2025`  | Understat season (start year of season)  |
| `ROLLING_WINDOW` | `10`    | Matches used for rolling xG ratings      |
| `MIN_MATCHES`    | `3`     | Min matches before using team's own data |

And in `src/monte_carlo.py`:

| Constant         | Default  | Description                              |
|------------------|----------|------------------------------------------|
| `N_SIMULATIONS`  | `100000` | Monte Carlo sample size                  |
| `HOME_ADVANTAGE` | `1.10`   | Multiplicative home uplift               |
| `DC_RHO`         | `-0.13`  | Dixon-Coles correction strength          |

---

## 📱 Dashboard tabs

| Tab        | Content                                                      |
|------------|--------------------------------------------------------------|
| 🔮 Upcoming | Probability bars + expected goals for next fixtures          |
| 📊 Results  | Completed matches with predicted vs actual outcomes          |
| 🏆 Stats    | Overall accuracy, outcome breakdown, Brier score             |
| ⚙️ Setup    | Enter Supabase credentials (stored in browser local storage) |

---

## ⚠️ Disclaimer

This is a statistical model for entertainment and educational purposes. Football contains significant randomness — even a well-calibrated model will be wrong ~55-60% of the time on individual match outcomes. Do not use for gambling.
