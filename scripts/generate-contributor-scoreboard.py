#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
PER_PAGE = 100


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a contributor scorecard from GitHub pull request data."
    )
    parser.add_argument(
        "--config",
        default="metrics/contributor-scoreboard/config.json",
        help="Path to the scorecard config JSON file.",
    )
    parser.add_argument(
        "--json-out",
        default="_work/contributor-scoreboard/scoreboard.json",
        help="Path to write the JSON output artifact.",
    )
    parser.add_argument(
        "--markdown-out",
        default="_work/contributor-scoreboard/scoreboard.md",
        help="Path to write the markdown summary.",
    )
    return parser.parse_args()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def matches_pattern(path: str, pattern: str) -> bool:
    normalized_path = path.lstrip("./")
    normalized_pattern = pattern.lstrip("./")
    if fnmatch(normalized_path, normalized_pattern):
        return True
    if normalized_pattern.startswith("**/") and fnmatch(
        normalized_path, normalized_pattern[3:]
    ):
        return True
    return False


def is_excluded_login(login: str, excluded_logins: set[str]) -> bool:
    normalized_login = login.lower()
    return normalized_login in excluded_logins or normalized_login.endswith("[bot]")


class GitHubApi:
    def __init__(self, org_name: str, token: str) -> None:
        self.org_name = org_name
        self.token = token

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{API_BASE}{path}"
        if params:
            query = urllib.parse.urlencode(params, doseq=True)
            url = f"{url}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def iter_search_items(
        self, repo_name: str, merged_since: datetime, max_items: int
    ) -> tuple[list[dict[str, Any]], bool]:
        query = (
            f"repo:{self.org_name}/{repo_name} "
            f"is:pr is:merged merged:>={merged_since.date().isoformat()}"
        )
        items: list[dict[str, Any]] = []
        page = 1
        truncated = False
        while len(items) < max_items:
            response = self.get_json(
                "/search/issues",
                {
                    "q": query,
                    "sort": "updated",
                    "order": "desc",
                    "per_page": PER_PAGE,
                    "page": page,
                },
            )
            batch = response.get("items", [])
            if not batch:
                break
            remaining = max_items - len(items)
            items.extend(batch[:remaining])
            if len(batch) < PER_PAGE:
                break
            if len(items) >= max_items:
                truncated = True
                break
            page += 1
        return items, truncated

    def get_pull(self, repo_name: str, number: int) -> dict[str, Any]:
        return self.get_json(f"/repos/{self.org_name}/{repo_name}/pulls/{number}")

    def iter_paged_list(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.get_json(path, {"per_page": PER_PAGE, "page": page})
            if not batch:
                break
            items.extend(batch)
            if len(batch) < PER_PAGE:
                break
            page += 1
        return items

    def get_pull_files(self, repo_name: str, number: int) -> list[dict[str, Any]]:
        return self.iter_paged_list(
            f"/repos/{self.org_name}/{repo_name}/pulls/{number}/files"
        )

    def get_pull_reviews(self, repo_name: str, number: int) -> list[dict[str, Any]]:
        return self.iter_paged_list(
            f"/repos/{self.org_name}/{repo_name}/pulls/{number}/reviews"
        )

    def get_pull_comments(self, repo_name: str, number: int) -> list[dict[str, Any]]:
        return self.iter_paged_list(
            f"/repos/{self.org_name}/{repo_name}/pulls/{number}/comments"
        )


def normalize_component(
    raw_values: dict[str, float], logins: list[str]
) -> dict[str, float]:
    max_value = max((raw_values.get(login, 0.0) for login in logins), default=0.0)
    if max_value <= 0:
        return {login: 0.0 for login in logins}
    return {
        login: (raw_values.get(login, 0.0) / max_value) * 100.0 for login in logins
    }


def render_markdown(
    payload: dict[str, Any],
    excluded_patterns: list[str],
    excluded_logins: list[str],
) -> str:
    lines = [
        "# Contributor Scoreboard",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Org: `{payload['org']}`",
        f"- Repos: `{', '.join(payload['repos'])}`",
        f"- Window: last `{payload['window_days']}` days",
        (
            "- Weights: "
            f"delivery `{payload['weights']['delivery']}` / "
            f"code `{payload['weights']['code']}` / "
            f"review `{payload['weights']['review']}`"
        ),
    ]
    if excluded_logins:
        lines.append(f"- Excluded logins: `{', '.join(excluded_logins)}`")
    lines.append("")

    contributors = payload["contributors"]
    if not contributors:
        lines.append("No contributors matched the configured window and scope.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| Rank | Contributor | Score | Delivery | Code | Review | Merged PRs | Filtered churn | Review points |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for entry in contributors:
        lines.append(
            "| "
            f"{entry['rank']} | "
            f"`{entry['login']}` | "
            f"{entry['score']:.1f} | "
            f"{entry['components']['delivery']:.1f} | "
            f"{entry['components']['code']:.1f} | "
            f"{entry['components']['review']:.1f} | "
            f"{entry['raw']['merged_prs']} | "
            f"{entry['raw']['filtered_churn']} | "
            f"{entry['raw']['review_points']:.1f} |"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "- Delivery counts merged pull requests in the configured repo scope.",
            "- Code uses additions + deletions after path exclusions.",
            "- Review uses review submissions and inline review comments on other authors' pull requests.",
            (
                "- Excluded code paths are matched with glob patterns from config, including: "
                f"`{', '.join(excluded_patterns[:8])}`"
                + (" ..." if len(excluded_patterns) > 8 else "")
            ),
        ]
    )

    if payload["truncated_repos"]:
        lines.append(
            "- Some repositories hit the configured PR cap and were truncated: "
            f"`{', '.join(payload['truncated_repos'])}`"
        )

    return "\n".join(lines) + "\n"


def process_pull_request(
    api: GitHubApi,
    repo_name: str,
    pr_number: int,
    merged_since: datetime,
    excluded_logins: set[str],
    excluded_patterns: list[str],
    review_weights: dict[str, float],
) -> dict[str, Any] | None:
    pr = api.get_pull(repo_name, pr_number)
    merged_at = parse_timestamp(pr.get("merged_at"))
    if merged_at is None or merged_at < merged_since:
        return None

    author_login = (pr.get("user") or {}).get("login", "")
    author_excluded = is_excluded_login(author_login, excluded_logins) if author_login else False

    filtered_churn = 0
    for changed_file in api.get_pull_files(repo_name, pr_number):
        filename = changed_file.get("filename", "")
        if any(matches_pattern(filename, pattern) for pattern in excluded_patterns):
            continue
        filtered_churn += int(changed_file.get("additions", 0)) + int(
            changed_file.get("deletions", 0)
        )

    review_totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"review_points": 0.0, "review_events": 0, "inline_comments": 0}
    )

    for review in api.get_pull_reviews(repo_name, pr_number):
        reviewer_login = ((review.get("user") or {}).get("login") or "").strip()
        if not reviewer_login or reviewer_login == author_login:
            continue
        if is_excluded_login(reviewer_login, excluded_logins):
            continue
        submitted_at = parse_timestamp(review.get("submitted_at"))
        if submitted_at is None or submitted_at < merged_since:
            continue
        state = (review.get("state") or "").upper()
        weight = float(review_weights.get(state, 0.0))
        if weight <= 0:
            continue
        review_totals[reviewer_login]["review_points"] += weight
        review_totals[reviewer_login]["review_events"] += 1

    inline_comment_weight = float(review_weights.get("inline_comment", 0.0))
    if inline_comment_weight > 0:
        for comment in api.get_pull_comments(repo_name, pr_number):
            commenter_login = ((comment.get("user") or {}).get("login") or "").strip()
            if not commenter_login or commenter_login == author_login:
                continue
            if is_excluded_login(commenter_login, excluded_logins):
                continue
            created_at = parse_timestamp(comment.get("created_at"))
            if created_at is None or created_at < merged_since:
                continue
            review_totals[commenter_login]["review_points"] += inline_comment_weight
            review_totals[commenter_login]["inline_comments"] += 1

    return {
        "author_login": author_login,
        "author_excluded": author_excluded,
        "filtered_churn": filtered_churn,
        "review_totals": review_totals,
    }


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))

    org_name = os.environ.get("ORG_NAME", "").strip()
    token = (
        os.environ.get("ORG_API_TOKEN")
        or os.environ.get("ORG_ADMIN_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )
    if not org_name:
        print("ORG_NAME is required", file=sys.stderr)
        return 1
    if not token:
        print(
            "A GitHub token is required via ORG_API_TOKEN, ORG_ADMIN_TOKEN, GH_TOKEN, or GITHUB_TOKEN",
            file=sys.stderr,
        )
        return 1

    weights = config["weights"]
    review_weights = config["review_weights"]
    repos = config["repos"]
    excluded_logins = {login.lower() for login in config.get("exclude_logins", [])}
    excluded_patterns = config.get("code_exclude_patterns", [])
    window_days = int(config["window_days"])
    max_pull_requests_per_repo = int(config.get("max_pull_requests_per_repo", 200))
    request_workers = int(config.get("request_workers", 8))

    now = datetime.now(timezone.utc)
    merged_since = now - timedelta(days=window_days)
    api = GitHubApi(org_name=org_name, token=token)

    delivery_raw: dict[str, float] = defaultdict(float)
    code_raw: dict[str, float] = defaultdict(float)
    review_raw: dict[str, float] = defaultdict(float)
    merged_prs_raw: dict[str, int] = defaultdict(int)
    filtered_churn_raw: dict[str, int] = defaultdict(int)
    review_events_raw: dict[str, int] = defaultdict(int)
    inline_comments_raw: dict[str, int] = defaultdict(int)
    contributors: set[str] = set()
    truncated_repos: list[str] = []

    for repo_name in repos:
        search_items, truncated = api.iter_search_items(
            repo_name, merged_since, max_pull_requests_per_repo
        )
        if truncated:
            truncated_repos.append(repo_name)
        if not search_items:
            continue

        print(
            f"[{repo_name}] evaluating {len(search_items)} merged pull requests",
            file=sys.stderr,
        )
        completed = 0
        with ThreadPoolExecutor(max_workers=request_workers) as executor:
            futures = [
                executor.submit(
                    process_pull_request,
                    api,
                    repo_name,
                    int(item["number"]),
                    merged_since,
                    excluded_logins,
                    excluded_patterns,
                    review_weights,
                )
                for item in search_items
            ]
            for future in as_completed(futures):
                completed += 1
                if completed == len(futures) or completed % 10 == 0:
                    print(
                        f"[{repo_name}] processed {completed}/{len(futures)} pull requests",
                        file=sys.stderr,
                    )
                result = future.result()
                if result is None:
                    continue

                author_login = result["author_login"]
                author_excluded = result["author_excluded"]
                filtered_churn = int(result["filtered_churn"])

                if author_login and not author_excluded:
                    contributors.add(author_login)
                    delivery_raw[author_login] += 1.0
                    merged_prs_raw[author_login] += 1
                    code_raw[author_login] += float(filtered_churn)
                    filtered_churn_raw[author_login] += filtered_churn

                for reviewer_login, review_summary in result["review_totals"].items():
                    contributors.add(reviewer_login)
                    review_raw[reviewer_login] += float(review_summary["review_points"])
                    review_events_raw[reviewer_login] += int(
                        review_summary["review_events"]
                    )
                    inline_comments_raw[reviewer_login] += int(
                        review_summary["inline_comments"]
                    )

    ranked_logins = sorted(contributors)
    delivery_component = normalize_component(delivery_raw, ranked_logins)
    code_component = normalize_component(code_raw, ranked_logins)
    review_component = normalize_component(review_raw, ranked_logins)

    contributor_entries: list[dict[str, Any]] = []
    for login in ranked_logins:
        score = (
            delivery_component[login] * float(weights["delivery"])
            + code_component[login] * float(weights["code"])
            + review_component[login] * float(weights["review"])
        )
        contributor_entries.append(
            {
                "login": login,
                "score": score,
                "components": {
                    "delivery": delivery_component[login],
                    "code": code_component[login],
                    "review": review_component[login],
                },
                "raw": {
                    "merged_prs": merged_prs_raw.get(login, 0),
                    "filtered_churn": filtered_churn_raw.get(login, 0),
                    "review_points": review_raw.get(login, 0.0),
                    "review_events": review_events_raw.get(login, 0),
                    "inline_review_comments": inline_comments_raw.get(login, 0),
                },
            }
        )

    contributor_entries.sort(
        key=lambda entry: (
            -entry["score"],
            -entry["components"]["delivery"],
            -entry["components"]["code"],
            -entry["components"]["review"],
            entry["login"].lower(),
        )
    )

    for index, entry in enumerate(contributor_entries, start=1):
        entry["rank"] = index

    payload = {
        "generated_at": now.isoformat(),
        "org": org_name,
        "repos": repos,
        "window_days": window_days,
        "weights": weights,
        "review_weights": review_weights,
        "truncated_repos": truncated_repos,
        "contributors": contributor_entries,
    }

    markdown = render_markdown(payload, excluded_patterns, sorted(excluded_logins))

    json_out = Path(args.json_out)
    markdown_out = Path(args.markdown_out)
    ensure_parent(json_out)
    ensure_parent(markdown_out)
    json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    markdown_out.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
