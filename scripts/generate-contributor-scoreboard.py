#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
import urllib.parse
import urllib.request
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


def occurs_within(timestamp: datetime | None, since: datetime | None) -> bool:
    return timestamp is not None and (since is None or timestamp >= since)


def empty_stats() -> dict[str, Any]:
    return {
        "delivery_raw": defaultdict(float),
        "code_raw": defaultdict(float),
        "review_raw": defaultdict(float),
        "merged_prs_raw": defaultdict(int),
        "filtered_churn_raw": defaultdict(int),
        "review_events_raw": defaultdict(int),
        "inline_comments_raw": defaultdict(int),
        "contributors": set(),
        "pull_requests_evaluated": 0,
        "truncated": False,
    }


def merge_counter(
    destination: dict[str, float] | dict[str, int], source: dict[str, float] | dict[str, int]
) -> None:
    for key, value in source.items():
        destination[key] += value


def merge_stats(destination: dict[str, Any], source: dict[str, Any]) -> None:
    merge_counter(destination["delivery_raw"], source["delivery_raw"])
    merge_counter(destination["code_raw"], source["code_raw"])
    merge_counter(destination["review_raw"], source["review_raw"])
    merge_counter(destination["merged_prs_raw"], source["merged_prs_raw"])
    merge_counter(destination["filtered_churn_raw"], source["filtered_churn_raw"])
    merge_counter(destination["review_events_raw"], source["review_events_raw"])
    merge_counter(destination["inline_comments_raw"], source["inline_comments_raw"])
    destination["contributors"].update(source["contributors"])
    destination["pull_requests_evaluated"] += int(source["pull_requests_evaluated"])
    destination["truncated"] = destination["truncated"] or bool(source["truncated"])


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

    def iter_merged_pulls(
        self, repo_name: str, merged_since: datetime | None, max_items: int
    ) -> tuple[list[int], bool]:
        numbers: list[int] = []
        page = 1
        truncated = False
        while len(numbers) < max_items:
            batch = self.get_json(
                f"/repos/{self.org_name}/{repo_name}/pulls",
                {
                    "state": "closed",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": PER_PAGE,
                    "page": page,
                },
            )
            if not batch:
                break
            for pull in batch:
                merged_at = parse_timestamp(pull.get("merged_at"))
                if not occurs_within(merged_at, merged_since):
                    continue
                numbers.append(int(pull["number"]))
                if len(numbers) >= max_items:
                    truncated = True
                    break
            if truncated or len(batch) < PER_PAGE:
                break
            page += 1
        return numbers, truncated

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


def build_contributor_entries(
    stats: dict[str, Any], weights: dict[str, float]
) -> list[dict[str, Any]]:
    ranked_logins = sorted(stats["contributors"])
    delivery_component = normalize_component(stats["delivery_raw"], ranked_logins)
    code_component = normalize_component(stats["code_raw"], ranked_logins)
    review_component = normalize_component(stats["review_raw"], ranked_logins)

    entries: list[dict[str, Any]] = []
    for login in ranked_logins:
        score = (
            delivery_component[login] * float(weights["delivery"])
            + code_component[login] * float(weights["code"])
            + review_component[login] * float(weights["review"])
        )
        entries.append(
            {
                "login": login,
                "score": score,
                "components": {
                    "delivery": delivery_component[login],
                    "code": code_component[login],
                    "review": review_component[login],
                },
                "raw": {
                    "merged_prs": stats["merged_prs_raw"].get(login, 0),
                    "filtered_churn": stats["filtered_churn_raw"].get(login, 0),
                    "review_points": stats["review_raw"].get(login, 0.0),
                    "review_events": stats["review_events_raw"].get(login, 0),
                    "inline_review_comments": stats["inline_comments_raw"].get(login, 0),
                },
            }
        )

    entries.sort(
        key=lambda entry: (
            -entry["score"],
            -entry["components"]["delivery"],
            -entry["components"]["code"],
            -entry["components"]["review"],
            entry["login"].lower(),
        )
    )

    for index, entry in enumerate(entries, start=1):
        entry["rank"] = index

    return entries


def build_scoreboard(
    scoreboard_config: dict[str, Any],
    repo_stats: dict[str, dict[str, Any]],
    weights: dict[str, float],
) -> dict[str, Any]:
    aggregate = empty_stats()
    truncated_repos: list[str] = []
    for repo_name in scoreboard_config["repos"]:
        stats = repo_stats[repo_name]
        merge_stats(aggregate, stats)
        if stats["truncated"]:
            truncated_repos.append(repo_name)

    contributors = build_contributor_entries(aggregate, weights)
    return {
        "key": scoreboard_config["key"],
        "title": scoreboard_config["title"],
        "repos": scoreboard_config["repos"],
        "pull_requests_evaluated": aggregate["pull_requests_evaluated"],
        "truncated_repos": truncated_repos,
        "contributors": contributors,
    }


def render_markdown(
    payload: dict[str, Any],
    excluded_patterns: list[str],
    excluded_logins: list[str],
) -> str:
    window_label = "all time" if payload["all_time"] else f"last `{payload['window_days']}` days"
    lines = [
        "# Contributor Scoreboard",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Org: `{payload['org']}`",
        f"- Included repos: `{', '.join(payload['repos'])}`",
        f"- Window: {window_label}",
        (
            "- Weights: "
            f"delivery `{payload['weights']['delivery']}` / "
            f"code `{payload['weights']['code']}` / "
            f"review `{payload['weights']['review']}`"
        ),
    ]
    if excluded_logins:
        lines.append(f"- Excluded logins: `{', '.join(excluded_logins)}`")

    for scoreboard in payload["scoreboards"]:
        lines.extend(
            [
                "",
                f"## {scoreboard['title']}",
                "",
                f"- Repos: `{', '.join(scoreboard['repos'])}`",
                f"- Merged PRs evaluated: `{scoreboard['pull_requests_evaluated']}`",
                f"- Contributors: `{len(scoreboard['contributors'])}`",
                "",
            ]
        )

        if not scoreboard["contributors"]:
            lines.append("No contributors matched this scoreboard scope.")
            continue

        lines.extend(
            [
                "| Rank | Contributor | Score | Delivery | Code | Review | Merged PRs | Filtered churn | Review points |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for entry in scoreboard["contributors"]:
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
        if scoreboard["truncated_repos"]:
            lines.extend(
                [
                    "",
                    "- Some repositories hit the configured PR cap and were truncated: "
                    f"`{', '.join(scoreboard['truncated_repos'])}`",
                ]
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

    return "\n".join(lines) + "\n"


def process_pull_request(
    api: GitHubApi,
    repo_name: str,
    pr_number: int,
    merged_since: datetime | None,
    excluded_logins: set[str],
    excluded_patterns: list[str],
    review_weights: dict[str, float],
) -> dict[str, Any] | None:
    pr = api.get_pull(repo_name, pr_number)
    merged_at = parse_timestamp(pr.get("merged_at"))
    if not occurs_within(merged_at, merged_since):
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
        if not occurs_within(submitted_at, merged_since):
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
            if not occurs_within(created_at, merged_since):
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
    excluded_logins = {login.lower() for login in config.get("exclude_logins", [])}
    excluded_patterns = config.get("code_exclude_patterns", [])
    window_days_raw = config.get("window_days")
    window_days = int(window_days_raw) if window_days_raw is not None else None
    scoreboard_configs = config.get("scoreboards") or [
        {"key": "default", "title": "Contributor Scoreboard", "repos": config["repos"]}
    ]
    repos = list(
        dict.fromkeys(
            repo_name
            for scoreboard in scoreboard_configs
            for repo_name in scoreboard["repos"]
        )
    )
    max_pull_requests_per_repo = int(config.get("max_pull_requests_per_repo", 1000))
    request_workers = int(config.get("request_workers", 8))

    now = datetime.now(timezone.utc)
    merged_since = None if window_days is None else now - timedelta(days=window_days)
    api = GitHubApi(org_name=org_name, token=token)

    repo_stats: dict[str, dict[str, Any]] = {}
    for repo_name in repos:
        stats = empty_stats()
        pull_numbers, truncated = api.iter_merged_pulls(
            repo_name, merged_since, max_pull_requests_per_repo
        )
        stats["truncated"] = truncated
        stats["pull_requests_evaluated"] = len(pull_numbers)
        repo_stats[repo_name] = stats

        if not pull_numbers:
            continue

        print(
            f"[{repo_name}] evaluating {len(pull_numbers)} merged pull requests",
            file=sys.stderr,
        )
        completed = 0
        with ThreadPoolExecutor(max_workers=request_workers) as executor:
            futures = [
                executor.submit(
                    process_pull_request,
                    api,
                    repo_name,
                    pr_number,
                    merged_since,
                    excluded_logins,
                    excluded_patterns,
                    review_weights,
                )
                for pr_number in pull_numbers
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
                    stats["contributors"].add(author_login)
                    stats["delivery_raw"][author_login] += 1.0
                    stats["merged_prs_raw"][author_login] += 1
                    stats["code_raw"][author_login] += float(filtered_churn)
                    stats["filtered_churn_raw"][author_login] += filtered_churn

                for reviewer_login, review_summary in result["review_totals"].items():
                    stats["contributors"].add(reviewer_login)
                    stats["review_raw"][reviewer_login] += float(
                        review_summary["review_points"]
                    )
                    stats["review_events_raw"][reviewer_login] += int(
                        review_summary["review_events"]
                    )
                    stats["inline_comments_raw"][reviewer_login] += int(
                        review_summary["inline_comments"]
                    )

    payload = {
        "generated_at": now.isoformat(),
        "org": org_name,
        "repos": repos,
        "all_time": window_days is None,
        "window_days": window_days,
        "weights": weights,
        "review_weights": review_weights,
        "scoreboards": [
            build_scoreboard(scoreboard_config, repo_stats, weights)
            for scoreboard_config in scoreboard_configs
        ],
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
