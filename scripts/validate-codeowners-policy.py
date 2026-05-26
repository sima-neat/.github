#!/usr/bin/env python3
"""Validate the centralized CODEOWNERS policy against access policy teams."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PERMISSION_LEVELS = {
    "pull": 1,
    "read": 1,
    "triage": 2,
    "push": 3,
    "write": 3,
    "maintain": 4,
    "admin": 5,
}
ALL_REPOSITORY_ROLE_LEVELS = {
    "all_repository_read": 1,
    "all_repository_triage": 2,
    "all_repository_write": 3,
    "all_repository_maintain": 4,
    "all_repository_admin": 5,
}
REQUIRED_OWNER_LEVEL = PERMISSION_LEVELS["write"]
REQUIRED_WATCHER_LEVEL = PERMISSION_LEVELS["write"]


class PolicyError(RuntimeError):
    """Policy validation failure."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PolicyError(f"{path} must contain a JSON object")
    return value


def require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyError(f"{path} must be a non-empty string")
    return value


def require_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise PolicyError(f"{path} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(require_string(item, f"{path}[{index}]"))
    if len(result) != len(set(result)):
        raise PolicyError(f"{path} must not contain duplicates")
    return result


def validate_access_policy(access_policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    teams = access_policy.get("teams")
    if access_policy.get("schema_version") != 1:
        raise PolicyError("access policy schema_version must be 1")
    if not isinstance(teams, dict) or not teams:
        raise PolicyError("access policy teams must be a non-empty object")
    return teams


def validate_codeowners_shape(codeowners_policy: dict[str, Any]) -> tuple[list[str], str, list[str], dict[str, Any]]:
    if codeowners_policy.get("schema_version") != 1:
        raise PolicyError("CODEOWNERS policy schema_version must be 1")
    require_string(codeowners_policy.get("organization"), "organization")

    target_repositories = require_string_list(
        codeowners_policy.get("target_repositories"), "target_repositories"
    )
    if not target_repositories:
        raise PolicyError("target_repositories must not be empty")

    default_owner = require_string(codeowners_policy.get("default_owner_team"), "default_owner_team")
    default_watchers = require_string_list(
        codeowners_policy.get("default_watcher_teams"), "default_watcher_teams"
    )

    repositories = codeowners_policy.get("repositories")
    if not isinstance(repositories, dict):
        raise PolicyError("repositories must be an object")

    unknown_repos = sorted(set(repositories) - set(target_repositories))
    if unknown_repos:
        raise PolicyError(f"repositories contains entries outside target_repositories: {unknown_repos}")

    for repo, config in repositories.items():
        if not isinstance(config, dict):
            raise PolicyError(f"repositories.{repo} must be an object")
        require_string(config.get("owner_team"), f"repositories.{repo}.owner_team")
        require_string_list(config.get("watcher_teams"), f"repositories.{repo}.watcher_teams")

    return target_repositories, default_owner, default_watchers, repositories


def team_permission_level(team: dict[str, Any], repo: str) -> int:
    level = 0
    repos = team.get("repos", {})
    if isinstance(repos, dict) and repo in repos:
        permission = repos[repo]
        level = max(level, PERMISSION_LEVELS.get(permission, 0))
    org_roles = team.get("org_roles", [])
    if isinstance(org_roles, list):
        for role in org_roles:
            level = max(level, ALL_REPOSITORY_ROLE_LEVELS.get(role, 0))
    return level


def require_team(teams: dict[str, dict[str, Any]], slug: str) -> dict[str, Any]:
    team = teams.get(slug)
    if team is None:
        raise PolicyError(f"team {slug!r} is referenced by CODEOWNERS policy but missing from access policy")
    if team.get("name") != slug:
        raise PolicyError(f"team {slug!r}: access policy name must match slug")
    if team.get("privacy") != "closed":
        raise PolicyError(f"team {slug!r}: privacy must be closed so GitHub can request/review it")
    return team


def validate_repo_mapping(
    *,
    repo: str,
    owner_team: str,
    watcher_teams: list[str],
    teams: dict[str, dict[str, Any]],
) -> None:
    owner = require_team(teams, owner_team)
    if team_permission_level(owner, repo) < REQUIRED_OWNER_LEVEL:
        raise PolicyError(f"owner team {owner_team!r} needs write-or-higher access to {repo!r}")

    if owner_team in watcher_teams:
        raise PolicyError(f"{repo}: owner team {owner_team!r} must not also be a watcher team")

    for watcher_team in watcher_teams:
        watcher = require_team(teams, watcher_team)
        if team_permission_level(watcher, repo) < REQUIRED_WATCHER_LEVEL:
            raise PolicyError(f"watcher team {watcher_team!r} needs write-or-higher access to {repo!r}")


def validate_policies(access_policy: dict[str, Any], codeowners_policy: dict[str, Any]) -> None:
    teams = validate_access_policy(access_policy)
    target_repositories, default_owner, default_watchers, repositories = validate_codeowners_shape(
        codeowners_policy
    )

    print("CODEOWNERS policy summary")
    print("=========================")
    for repo in target_repositories:
        repo_config = repositories.get(repo, {})
        owner_team = repo_config.get("owner_team", default_owner)
        watcher_teams = repo_config.get("watcher_teams", default_watchers)
        validate_repo_mapping(
            repo=repo,
            owner_team=owner_team,
            watcher_teams=watcher_teams,
            teams=teams,
        )
        watchers = ", ".join(watcher_teams) or "none"
        source = "explicit" if repo in repositories else "default"
        print(f"- {repo}: owner={owner_team}, watchers={watchers} ({source})")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--access-policy",
        default="policies/access/config.json",
        help="Path to access policy JSON.",
    )
    parser.add_argument(
        "--codeowners-policy",
        default="policies/codeowners/config.json",
        help="Path to CODEOWNERS policy JSON.",
    )
    args = parser.parse_args(argv)

    try:
        validate_policies(load_json(Path(args.access_policy)), load_json(Path(args.codeowners_policy)))
        return 0
    except PolicyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
