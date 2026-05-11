# PostHog Review Influence Dashboard

A prototype engineering impact dashboard for the [PostHog/posthog](https://github.com/PostHog/posthog) repository. Instead of counting lines of code, it measures **Review Influence** — the leverage an engineer creates through high-quality code review.

![Dashboard preview](https://img.shields.io/badge/stack-Python%20%7C%20Flask%20%7C%20D3.js%20%7C%20Chart.js-blueviolet)

---

## What it measures

A reviewer's **Review Influence Score** is the sum of points earned across every review they submitted on a merged PR in the last 90 days:

```
points = PR Complexity × Subsystem Criticality × (1 + Review Depth) × Acceptance Weight × Sample Weight
```

| Signal                    | Range        | How it's computed                                                                                                                                |
| ------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **PR Complexity**         | 1–5          | Files changed + line delta from PR metadata (no extra API call)                                                                                  |
| **Subsystem Criticality** | 1–3          | Highest-weight path prefix in the first 30 changed files. `plugin-server`, `hogql` → 3; `posthog/api`, `posthog/models` → 2; everything else → 1 |
| **Review Depth**          | bonus 0–2.5  | +1.0 for review body > 30 words · +1.5 for `CHANGES_REQUESTED` · +0.5 for `APPROVED`                                                             |
| **Acceptance Weight**     | 1.0× or 1.5× | 1.5 if any reviewer requested changes (proxy that the author revised the code before merge)                                                      |
| **Sample Weight**         | 1.0× or 4.0× | Inverse-probability correction for stratified sampling (see below)                                                                               |

**Network Reach** (unique authors helped, subsystems covered) is tracked as a supplementary stat shown in the leaderboard but not folded into the score.

---

## Architecture

The project uses a **Freeze Frame** approach — no live database required.

```
scraper.py  ──→  data.json  ──→  app.py  ──→  index.html
  (run once)      (static)     (Flask)      (D3 + Chart.js)
```

1. **`scraper.py`** — run locally once to pull data and write `data.json`.
2. **`app.py`** — Flask app that loads `data.json` at startup and exposes `/api/data`.
3. **`templates/index.html`** — single-page dashboard; tab navigation and all charts are rendered client-side without page reloads.

---

## Scraper design

### GitHub API efficiency

The scraper is built to minimise API calls on a large, active repository:

- **GitHub Search API** (`is:pr is:merged merged:>DATE`) pre-filters to only merged PRs in the 90-day window, avoiding iteration through all closed PRs.
- **PR metadata** (`changed_files`, `additions`, `deletions`) is used for complexity scoring — these fields come free with the search result.
- **File scan capped at one page** (≤ 30 files) with an early-exit once the maximum criticality weight is found.
- **No commit fetches** — whether the author revised their code is inferred from `CHANGES_REQUESTED` reviews rather than comparing commit timestamps.
- **No inline comment fetches** — review body depth from `get_reviews()` is sufficient.

This yields roughly **3 API calls per PR** (search page amortised + `as_pull_request()` + `get_files()` first page + `get_reviews()`), down from ~5–6 in a naive implementation.

### Stratified sampling

PRs are split into two strata using only the free metadata fields:

| Stratum | Condition                                 | Sample rate  | Sample Weight |
| ------- | ----------------------------------------- | ------------ | ------------- |
| Large   | ≥ 10 files changed **or** ≥ 400 net lines | 100%         | 1.0           |
| Small   | below both thresholds                     | 25% (random) | 4.0           |

Each sampled small PR is scored with a ×4 inverse-probability weight so cumulative scores remain statistically unbiased estimates of what a full scan would produce.

---

## Dashboard tabs

| Tab                      | Visualisation                                                                                    |
| ------------------------ | ------------------------------------------------------------------------------------------------ |
| **Overview**             | Metric cards, scoring formula with variable definitions, stratified sampling explanation         |
| **Reviewer Leaderboard** | Ranked table with score bar, PRs reviewed, unique authors, subsystems covered                    |
| **Review Network**       | D3 force-directed graph — node size = influence score, edge weight = review frequency; draggable |
| **Influence Trend**      | Chart.js line chart of total influence points per ISO week                                       |
| **Subsystem Coverage**   | Chart.js bar chart of review density by codebase area                                            |

---

## Setup

### Prerequisites

- Python 3.9+
- A GitHub personal access token with `repo` (read) scope

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure

```bash
touch .env
# Open .env and add: GITHUB_TOKEN=ghp_your_token_here
```

### Run the scraper

```bash
python scraper.py
```

This writes `data.json` to the project root. On a large repo like PostHog it takes roughly 5–15 minutes depending on GitHub API rate limits. Progress is printed every 25 PRs.

### Start the dashboard

```bash
python app.py
```

Open [http://localhost:5000](http://localhost:5000).

---

## Deployment on Render

1. Push the repository to GitHub (ensure `data.json` is committed).
2. Create a new **Web Service** on [Render](https://render.com) pointing at the repo.
3. Render will automatically use the `Procfile`:
   ```
   web: gunicorn app:app
   ```

---

## Project structure

```
engineering-impact-dashboard/
├── scraper.py          # Data collection — run once locally
├── app.py              # Flask backend
├── templates/
│   └── index.html      # Single-page dashboard (D3 + Chart.js)
├── data.json           # Generated by scraper (gitignored)
├── requirements.txt
├── Procfile            # Render deployment
└── .env                # Token configuration
```

---

## Limitations & future work

- **Sampling variance** — the 25% small-PR sample means per-reviewer scores have some noise run-to-run. Increasing `SMALL_PR_SAMPLE_RATE` or removing sampling reduces this at the cost of more API calls.
- **Subsystem detection is approximate** — only the first 30 changed files are inspected. PRs that touch a critical subsystem only in later files will be classified as `other`.
- **Review depth proxy is shallow** — word count and review state are blunt instruments. Sentiment analysis or threading depth would be more precise but require significantly more data.
- **Bot accounts** — automated reviewer accounts (Dependabot, etc.) are not filtered and will appear in the leaderboard if they submit reviews.
