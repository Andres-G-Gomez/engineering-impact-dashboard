"""
PostHog Review Influence Scraper
Fetches 90 days of merged PRs via GitHub Search API and writes data.json.

Usage:
    GITHUB_TOKEN=ghp_... python scraper.py

API call budget per PR (down from ~5):
  1. search pagination  (amortised across 100 results per page)
  2. as_pull_request()  → changed_files / additions / deletions
  3. get_files()        → first page only (≤30 paths), for subsystem detection
  4. get_reviews()      → review states + bodies
"""

import os
import json
import re
import random
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from github import Github
from dotenv import load_dotenv

load_dotenv()

REPO = "PostHog/posthog"
DAYS_BACK = 90
MAX_FILES_SCAN = 30       # one API page; stop early once highest-weight subsystem is found
LARGE_PR_FILES = 10       # always process PRs at or above this file-change count
LARGE_PR_DELTA = 400      # always process PRs at or above this line-delta
SMALL_PR_SAMPLE_RATE = 0.25  # fraction of small PRs to process; points scaled by 1/rate
CRITICAL_PATHS = {
    "plugin-server": 3,
    "hogql": 3,
    "posthog/api": 2,
    "posthog/models": 2,
}


def get_pr_complexity(pr):
    """Score 1-5 from PR metadata fields — no extra API call needed."""
    files = pr.changed_files or 0
    delta = (pr.additions or 0) + (pr.deletions or 0)
    if files > 60 or delta > 4000: return 5
    if files > 30 or delta > 1500: return 4
    if files > 15 or delta > 600:  return 3
    if files > 5  or delta > 200:  return 2
    return 1


def scan_subsystem(pr):
    """
    Return (name, weight) for the highest-criticality subsystem touched.
    Reads at most MAX_FILES_SCAN filenames (one API page) and stops early
    once the maximum possible weight (3) is found.
    """
    best_name, best_weight = "other", 1
    for i, f in enumerate(pr.get_files()):
        if i >= MAX_FILES_SCAN:
            break
        path = f.filename.lower()
        for prefix, w in CRITICAL_PATHS.items():
            if path.startswith(prefix) and w > best_weight:
                best_name, best_weight = prefix, w
        if best_weight == 3:  # can't go higher; skip remaining files
            break
    return best_name, best_weight


def word_count(text):
    return len(re.findall(r'\w+', text or ""))


def scrape():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise SystemExit("Set GITHUB_TOKEN environment variable.")

    g = Github(token, per_page=100)
    since_str = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).date().isoformat()

    # Search API returns only merged PRs within the window — no post-fetch date filtering needed.
    query = f"repo:{REPO} is:pr is:merged merged:>{since_str}"
    print(f"Searching: {query}")
    search_results = g.search_issues(query, sort="updated", order="desc")

    reviewer_scores    = defaultdict(float)
    reviewer_prs       = defaultdict(set)
    reviewer_authors   = defaultdict(set)
    reviewer_subsystems = defaultdict(set)
    review_links       = defaultdict(lambda: defaultdict(int))
    weekly_points      = defaultdict(float)
    subsystem_coverage = defaultdict(int)

    processed = skipped = 0
    for issue in search_results:
        # as_pull_request() is cheap: returns changed_files/additions/deletions
        # which is all we need for the sampling decision.
        pr = issue.as_pull_request()

        files = pr.changed_files or 0
        delta = (pr.additions or 0) + (pr.deletions or 0)
        is_large = files >= LARGE_PR_FILES or delta >= LARGE_PR_DELTA

        if not is_large and random.random() > SMALL_PR_SAMPLE_RATE:
            skipped += 1
            continue

        # Inverse-probability weight keeps scores unbiased: each sampled small PR
        # represents 1/SMALL_PR_SAMPLE_RATE real PRs in the population.
        sample_weight = 1.0 if is_large else 1.0 / SMALL_PR_SAMPLE_RATE

        processed += 1
        if processed % 25 == 0:
            print(f"  processed {processed} PRs (skipped {skipped} small) …")

        author = pr.user.login if pr.user else "ghost"

        complexity            = get_pr_complexity(pr)
        subsystem, sys_weight = scan_subsystem(pr)  # capped at one file-list page
        reviews               = list(pr.get_reviews())

        # Acceptance heuristic: if any reviewer requested changes, the author
        # almost certainly updated the code before merge — no commit fetch needed.
        had_changes_requested = any(
            rv.state == "CHANGES_REQUESTED" and rv.user and rv.user.login != author
            for rv in reviews
        )
        acceptance_multiplier = 1.5 if had_changes_requested else 1.0

        for rv in reviews:
            if rv.user is None:
                continue
            reviewer = rv.user.login
            if reviewer == author:
                continue

            depth_bonus = 0.0
            if word_count(rv.body) > 30:        depth_bonus += 1.0
            if rv.state == "CHANGES_REQUESTED":  depth_bonus += 1.5
            if rv.state == "APPROVED":           depth_bonus += 0.5

            points = complexity * sys_weight * (1 + depth_bonus) * acceptance_multiplier * sample_weight

            reviewer_scores[reviewer]      += points
            reviewer_prs[reviewer].add(pr.number)
            reviewer_authors[reviewer].add(author)
            reviewer_subsystems[reviewer].add(subsystem)
            review_links[reviewer][author] += 1
            subsystem_coverage[subsystem]  += 1

            if rv.submitted_at:
                weekly_points[rv.submitted_at.strftime("%Y-W%W")] += points

    print(f"Scraped {processed} PRs ({skipped} small PRs sampled out). Building output …")

    leaderboard = [
        {
            "login":            eng,
            "score":            round(score, 1),
            "prs_reviewed":     len(reviewer_prs[eng]),
            "authors_reviewed": len(reviewer_authors[eng]),
            "subsystems":       len(reviewer_subsystems[eng]),
            "avatar_url":       f"https://github.com/{eng}.png?size=40",
        }
        for eng, score in sorted(reviewer_scores.items(), key=lambda x: -x[1])
    ]

    all_nodes = set(reviewer_scores) | {t for targets in review_links.values() for t in targets}

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo":         REPO,
        "days_back":    DAYS_BACK,
        "leaderboard":  leaderboard[:50],
        "graph": {
            "nodes": [{"id": eng, "score": round(reviewer_scores.get(eng, 0), 1)} for eng in all_nodes],
            "links": [
                {"source": reviewer, "target": author, "value": count}
                for reviewer, targets in review_links.items()
                for author, count in targets.items()
            ],
        },
        "trends":   [{"week": w, "points": round(p, 1)} for w, p in sorted(weekly_points.items())],
        "coverage": [{"subsystem": s, "reviews": c} for s, c in sorted(subsystem_coverage.items(), key=lambda x: -x[1])],
    }

    with open("data.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"Written data.json — {len(leaderboard)} engineers, {len(output['graph']['links'])} review links.")


if __name__ == "__main__":
    scrape()
