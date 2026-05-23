#!/usr/bin/env python3
"""Validate and summarize the team-centric access policy."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


VALID_PERMISSIONS = {"pull", "read", "triage", "push", "write", "maintain", "admin"}
VALID_PRIVACY = {"closed", "secret"}
VALID_ORG_ROLES = {
    "all_repository_read",
    "all_repository_write",
    "all_repository_triage",
    "all_repository_maintain",
    "all_repository_admin",
    "apps_manager",
    "ci_cd_admin",
    "security_manager",
}


def normalized_permission(value: str) -> str:
    if value == "read":
        return "pull"
    if value == "write":
        return "push"
    return value


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def load_policy(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid JSON: {exc}")


def validate_policy(policy: dict) -> None:
    if policy.get("schema_version") != 1:
        fail("schema_version must be 1")

    teams = policy.get("teams")
    if not isinstance(teams, dict) or not teams:
        fail("teams must be a non-empty object")

    for slug, team in teams.items():
        if team.get("name") != slug:
            fail(f"team {slug}: name must match slug")
        if team.get("privacy") not in VALID_PRIVACY:
            fail(f"team {slug}: privacy must be one of {sorted(VALID_PRIVACY)}")

        for role in team.get("org_roles", []):
            if role not in VALID_ORG_ROLES:
                fail(f"team {slug}: invalid org role {role}")

        repos = team.get("repos", {})
        if not isinstance(repos, dict):
            fail(f"team {slug}: repos must be an object")
        for repo, permission in repos.items():
            if permission not in VALID_PERMISSIONS:
                fail(f"team {slug}: invalid permission {permission} for repo {repo}")

    direct = policy.get("direct_assignments", {})
    exceptions = direct.get("exceptions", {})
    if direct.get("allowed") is False and not isinstance(exceptions, dict):
        fail("direct_assignments.exceptions must be an object")

    for repo, users in exceptions.items():
        if not isinstance(users, list):
            fail(f"direct assignment exceptions for {repo} must be a list")
        for item in users:
            for key in ("username", "permission", "reason", "expires"):
                if not item.get(key):
                    fail(f"direct assignment exception for {repo} is missing {key}")
            if item["permission"] not in VALID_PERMISSIONS:
                fail(f"direct assignment exception for {repo}: invalid permission {item['permission']}")


def summarize(policy: dict) -> None:
    team_repo_grants: dict[str, list[tuple[str, str]]] = {}
    repo_to_teams: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for slug, team in sorted(policy["teams"].items()):
        grants = []
        for repo, permission in sorted(team.get("repos", {}).items()):
            permission = normalized_permission(permission)
            grants.append((repo, permission))
            repo_to_teams[repo].append((slug, permission))
        team_repo_grants[slug] = grants

    print("Access policy summary")
    print("=====================")
    print()
    print("Managed teams:")
    for slug, team in sorted(policy["teams"].items()):
        roles = ", ".join(team.get("org_roles", [])) or "none"
        print(f"- {slug}: {team.get('description', '')} (org roles: {roles})")

    print()
    print("Repository grants:")
    for repo in sorted(repo_to_teams):
        grants = ", ".join(f"{team}:{perm}" for team, perm in sorted(repo_to_teams[repo]))
        print(f"- {repo}: {grants}")

    legacy = policy.get("legacy_teams", {})
    if legacy:
        print()
        print("Legacy teams preserved during transition:")
        for slug, item in sorted(legacy.items()):
            print(f"- {slug}: {item.get('status', 'unknown')} ({item.get('reason', '')})")

    direct = policy.get("direct_assignments", {})
    exceptions = direct.get("exceptions", {})
    if exceptions:
        print()
        print("Direct assignment exceptions:")
        for repo, users in sorted(exceptions.items()):
            grants = ", ".join(f"{item['username']}:{item['permission']} until {item['expires']}" for item in users)
            print(f"- {repo}: {grants}")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("policies/access/config.json")
    policy = load_policy(path)
    validate_policy(policy)
    summarize(policy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

