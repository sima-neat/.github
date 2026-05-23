#!/usr/bin/env python3
"""Validate, report, and apply the sima-neat organization access policy."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
PER_PAGE = 100
SSL_CONTEXT = ssl.create_default_context()
try:
    import certifi  # type: ignore[import-not-found]

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass

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
ORG_ROLE_ALIASES = {
    "all_repository_read": "all_repo_read",
    "all_repository_write": "all_repo_write",
    "all_repository_triage": "all_repo_triage",
    "all_repository_maintain": "all_repo_maintain",
    "all_repository_admin": "all_repo_admin",
    "apps_manager": "app_manager",
}


class PolicyError(RuntimeError):
    """Policy validation or GitHub API failure."""


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def normalized_permission(value: str) -> str:
    return {"read": "pull", "write": "push"}.get(value, value)


def api_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "sima-neat-access-policy-sync",
    }


def request_json(
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    allow_404: bool = False,
) -> Any:
    data = None
    headers = api_headers(token)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404 and allow_404:
            return None
        raise PolicyError(f"GitHub API {method} {path} failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise PolicyError(f"GitHub API {method} {path} failed: {exc}") from exc

    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body.decode("utf-8", errors="replace")


def paginate(token: str, path: str) -> list[Any]:
    results: list[Any] = []
    separator = "&" if "?" in path else "?"
    page = 1
    while True:
        items = request_json(token, "GET", f"{path}{separator}per_page={PER_PAGE}&page={page}")
        if not items:
            break
        if not isinstance(items, list):
            raise PolicyError(f"Expected list response for {path}, got {type(items).__name__}")
        results.extend(items)
        if len(items) < PER_PAGE:
            break
        page += 1
    return results


def load_policy(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyError(f"{path} is not valid JSON: {exc}") from exc


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema_version") != 1:
        raise PolicyError("schema_version must be 1")

    teams = policy.get("teams")
    if not isinstance(teams, dict) or not teams:
        raise PolicyError("teams must be a non-empty object")

    for slug, team in teams.items():
        if team.get("name") != slug:
            raise PolicyError(f"team {slug}: name must match slug")
        if team.get("privacy") not in VALID_PRIVACY:
            raise PolicyError(f"team {slug}: privacy must be one of {sorted(VALID_PRIVACY)}")
        if not isinstance(team.get("description", ""), str):
            raise PolicyError(f"team {slug}: description must be a string")

        org_roles = team.get("org_roles", [])
        if not isinstance(org_roles, list):
            raise PolicyError(f"team {slug}: org_roles must be a list")
        for role in org_roles:
            if role not in VALID_ORG_ROLES:
                raise PolicyError(f"team {slug}: invalid org role {role}")

        repos = team.get("repos", {})
        if not isinstance(repos, dict):
            raise PolicyError(f"team {slug}: repos must be an object")
        for repo, permission in repos.items():
            if permission not in VALID_PERMISSIONS:
                raise PolicyError(f"team {slug}: invalid permission {permission} for repo {repo}")

    legacy = policy.get("legacy_teams", {})
    if not isinstance(legacy, dict):
        raise PolicyError("legacy_teams must be an object")
    for slug, item in legacy.items():
        if not isinstance(item, dict):
            raise PolicyError(f"legacy team {slug}: entry must be an object")
        if "preserve" in item and not isinstance(item["preserve"], bool):
            raise PolicyError(f"legacy team {slug}: preserve must be boolean")

    prune = policy.get("prune_unmanaged", {})
    if not isinstance(prune, dict):
        raise PolicyError("prune_unmanaged must be an object")
    if "teams" in prune and not isinstance(prune["teams"], bool):
        raise PolicyError("prune_unmanaged.teams must be boolean")


def summarize(policy: dict[str, Any]) -> None:
    repo_to_teams: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for slug, team in sorted(policy["teams"].items()):
        for repo, permission in sorted(team.get("repos", {}).items()):
            repo_to_teams[repo].append((slug, normalized_permission(permission)))

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


def require_token(token: str | None) -> str:
    if not token:
        raise PolicyError("Token is required for apply mode. Set ORG_ADMIN_TOKEN or GITHUB_TOKEN.")
    return token


def ensure_team(token: str, org: str, slug: str, team: dict[str, Any]) -> None:
    existing = request_json(token, "GET", f"/orgs/{quote(org)}/teams/{quote(slug)}", allow_404=True)
    payload = {"name": team["name"], "description": team.get("description", ""), "privacy": team["privacy"]}
    if existing is None:
        print(f"Creating team: {slug}")
        request_json(token, "POST", f"/orgs/{quote(org)}/teams", payload)
    else:
        print(f"Updating team: {slug}")
        request_json(token, "PATCH", f"/orgs/{quote(org)}/teams/{quote(slug)}", payload)


def get_org_role_ids(token: str, org: str) -> dict[str, int]:
    response = request_json(token, "GET", f"/orgs/{quote(org)}/organization-roles")
    roles = response.get("roles", []) if isinstance(response, dict) else []
    role_ids: dict[str, int] = {}
    for role in roles:
        name = role.get("name")
        role_id = role.get("id")
        if isinstance(name, str) and isinstance(role_id, int):
            role_ids[name] = role_id
    return role_ids


def ensure_org_roles(token: str, org: str, slug: str, roles: list[str], role_ids: dict[str, int]) -> None:
    for configured_role in roles:
        api_role = ORG_ROLE_ALIASES.get(configured_role, configured_role)
        role_id = role_ids.get(api_role)
        if role_id is None:
            raise PolicyError(f"GitHub organization role {configured_role} ({api_role}) was not found")
        print(f"Ensuring org role: {slug} -> {configured_role}")
        request_json(token, "PUT", f"/orgs/{quote(org)}/organization-roles/teams/{quote(slug)}/{role_id}")


def ensure_team_repo_grants(token: str, org: str, slug: str, repos: dict[str, str]) -> None:
    for repo, permission in sorted(repos.items()):
        normalized = normalized_permission(permission)
        print(f"Ensuring team repo permission: {slug} -> {repo}:{normalized}")
        request_json(
            token,
            "PUT",
            f"/orgs/{quote(org)}/teams/{quote(slug)}/repos/{quote(org)}/{quote(repo)}",
            {"permission": normalized},
        )


def desired_repo_teams(policy: dict[str, Any]) -> dict[str, set[str]]:
    desired: dict[str, set[str]] = defaultdict(set)
    for slug, team in policy["teams"].items():
        for repo in team.get("repos", {}):
            desired[repo].add(slug)
    return desired


def prune_unmanaged_team_grants(token: str, org: str, policy: dict[str, Any]) -> None:
    legacy_preserved = {
        slug
        for slug, item in policy.get("legacy_teams", {}).items()
        if isinstance(item, dict) and item.get("preserve", False)
    }
    desired = desired_repo_teams(policy)
    for repo, desired_slugs in sorted(desired.items()):
        teams = paginate(token, f"/repos/{quote(org)}/{quote(repo)}/teams")
        for item in teams:
            slug = item.get("slug")
            if not slug or slug in desired_slugs or slug in legacy_preserved:
                continue
            print(f"Removing unmanaged team repo permission: {slug} -> {repo}")
            request_json(
                token,
                "DELETE",
                f"/orgs/{quote(org)}/teams/{quote(slug)}/repos/{quote(org)}/{quote(repo)}",
                allow_404=True,
            )


def apply_policy(policy: dict[str, Any], org: str, token: str | None) -> None:
    api_token = require_token(token)
    print(f"Applying access policy to organization: {org}")
    role_ids = get_org_role_ids(api_token, org)

    for slug, team in sorted(policy["teams"].items()):
        ensure_team(api_token, org, slug, team)
        ensure_org_roles(api_token, org, slug, team.get("org_roles", []), role_ids)
        ensure_team_repo_grants(api_token, org, slug, team.get("repos", {}))

    prune = policy.get("prune_unmanaged", {})
    if prune.get("teams", False):
        prune_unmanaged_team_grants(api_token, org, policy)
    else:
        print("Skipping unmanaged team pruning because prune_unmanaged.teams=false")

    print("Access policy apply complete.")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("report", "apply"), default=os.getenv("MODE", "report"))
    parser.add_argument(
        "--policy-file",
        default=os.getenv("POLICY_FILE", "policies/access/config.json"),
        help="Path to access policy JSON.",
    )
    parser.add_argument("--org", default=os.getenv("ORG_NAME", "sima-neat"), help="GitHub organization name.")
    args = parser.parse_args(argv)

    try:
        policy = load_policy(Path(args.policy_file))
        validate_policy(policy)
        summarize(policy)
        if args.mode == "apply":
            token = os.getenv("ORG_ADMIN_TOKEN") or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
            print()
            apply_policy(policy, args.org, token)
        return 0
    except PolicyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
