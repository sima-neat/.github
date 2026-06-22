#!/usr/bin/env python3
"""Validate release project issues before release artifacts are created."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", ""}:
        return False
    raise argparse.ArgumentTypeError(f"expected boolean value, got: {value}")


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def log_group(title: str) -> None:
    print(f"::group::{title}")


def end_group() -> None:
    print("::endgroup::")


@dataclass(frozen=True)
class FieldRef:
    id: str
    name: str
    typename: str
    options: dict[str, str]


@dataclass
class ReleaseIssue:
    item_id: str
    title: str
    number: int
    url: str
    repository: str
    state: str
    status: str | None
    release_status: str | None


class GitHubGraphQL:
    def __init__(self, token: str) -> None:
        self.token = token

    def request(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            GITHUB_GRAPHQL_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            die(f"GitHub GraphQL request failed with HTTP {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            die(f"GitHub GraphQL request failed: {exc}")

        if payload.get("errors"):
            for error in payload["errors"]:
                if (
                    error.get("type") == "NOT_FOUND"
                    and error.get("path") == ["organization", "projectV2"]
                ):
                    die(
                        "GitHub token cannot access the requested organization project. "
                        "Confirm the Release Engineering GitHub App is installed on the "
                        "organization and has Organization permissions -> Projects: read/write. "
                        f"Raw error: {json.dumps(error)}"
                    )
            die(f"GitHub GraphQL returned errors: {json.dumps(payload['errors'], indent=2)}")
        return payload["data"]


PROJECT_QUERY = """
query Project($owner: String!, $number: Int!, $fieldsAfter: String) {
  organization(login: $owner) {
    projectV2(number: $number) {
      id
      title
      url
      fields(first: 100, after: $fieldsAfter) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          __typename
          ... on ProjectV2FieldCommon {
            id
            name
          }
          ... on ProjectV2SingleSelectField {
            options {
              id
              name
            }
          }
        }
      }
    }
  }
}
"""


ITEMS_QUERY = """
query ProjectItems($projectId: ID!, $itemsAfter: String) {
  node(id: $projectId) {
    ... on ProjectV2 {
      items(first: 100, after: $itemsAfter) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          content {
            __typename
            ... on Issue {
              title
              number
              url
              state
              repository {
                nameWithOwner
              }
            }
            ... on PullRequest {
              title
              number
              url
              state
              repository {
                nameWithOwner
              }
            }
            ... on DraftIssue {
              title
            }
          }
          fieldValues(first: 100) {
            nodes {
              __typename
              ... on ProjectV2ItemFieldTextValue {
                text
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
              ... on ProjectV2ItemFieldNumberValue {
                number
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
              ... on ProjectV2ItemFieldDateValue {
                date
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                optionId
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
              ... on ProjectV2ItemFieldIterationValue {
                title
                startDate
                duration
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


UPDATE_FIELD_MUTATION = """
mutation UpdateField($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(
    input: {
      projectId: $projectId
      itemId: $itemId
      fieldId: $fieldId
      value: { singleSelectOptionId: $optionId }
    }
  ) {
    projectV2Item {
      id
    }
  }
}
"""


def get_project(client: GitHubGraphQL, owner: str, number: int) -> tuple[dict[str, Any], dict[str, FieldRef]]:
    project: dict[str, Any] | None = None
    fields: dict[str, FieldRef] = {}
    cursor: str | None = None

    while True:
        data = client.request(PROJECT_QUERY, {"owner": owner, "number": number, "fieldsAfter": cursor})
        org = data.get("organization")
        if not org:
            die(f"organization not found or not accessible: {owner}")
        project = org.get("projectV2")
        if not project:
            die(f"project not found or not accessible: {owner}/{number}")

        for node in project["fields"]["nodes"]:
            if not node or not node.get("name"):
                continue
            options = {
                option["name"]: option["id"]
                for option in node.get("options", [])
                if option.get("name") and option.get("id")
            }
            fields[node["name"]] = FieldRef(
                id=node["id"],
                name=node["name"],
                typename=node["__typename"],
                options=options,
            )

        page_info = project["fields"]["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    if project is None:
        die(f"project not found or not accessible: {owner}/{number}")
    return project, fields


def field_value(node: dict[str, Any]) -> tuple[str | None, str | None]:
    field = node.get("field") or {}
    name = field.get("name")
    typename = node.get("__typename")
    value: str | None = None

    if typename == "ProjectV2ItemFieldSingleSelectValue":
        value = node.get("name")
    elif typename == "ProjectV2ItemFieldTextValue":
        value = node.get("text")
    elif typename == "ProjectV2ItemFieldNumberValue":
        number = node.get("number")
        value = None if number is None else str(number)
    elif typename == "ProjectV2ItemFieldDateValue":
        value = node.get("date")
    elif typename == "ProjectV2ItemFieldIterationValue":
        value = node.get("title")

    return name, value


def get_items(client: GitHubGraphQL, project_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = client.request(ITEMS_QUERY, {"projectId": project_id, "itemsAfter": cursor})
        project = data.get("node")
        if not project:
            die(f"project node not found: {project_id}")
        page = project["items"]
        items.extend(node for node in page["nodes"] if node)
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return items


def require_field(fields: dict[str, FieldRef], name: str) -> FieldRef:
    field = fields.get(name)
    if not field:
        available = ", ".join(sorted(fields))
        die(f"required project field not found: {name}. Available fields: {available}")
    return field


def require_single_select_option(field: FieldRef, value: str) -> str:
    if field.typename != "ProjectV2SingleSelectField":
        die(f"field must be a single-select field for updates: {field.name} ({field.typename})")
    option_id = field.options.get(value)
    if not option_id:
        available = ", ".join(sorted(field.options))
        die(f"option not found on field {field.name}: {value}. Available options: {available}")
    return option_id


def collect_release_issues(
    items: list[dict[str, Any]],
    release_version: str,
    release_field_name: str,
    status_field_name: str,
    release_status_field_name: str,
) -> tuple[list[ReleaseIssue], list[str]]:
    issues: list[ReleaseIssue] = []
    skipped: list[str] = []

    for item in items:
        values = {}
        for value_node in item["fieldValues"]["nodes"]:
            if not value_node:
                continue
            name, value = field_value(value_node)
            if name:
                values[name] = value

        if values.get(release_field_name) != release_version:
            continue

        content = item.get("content")
        if not content:
            skipped.append(f"{item['id']} has no content")
            continue
        typename = content.get("__typename")
        if typename != "Issue":
            title = content.get("title") or "(untitled)"
            url = content.get("url") or item["id"]
            skipped.append(f"{typename}: {title} {url}")
            continue

        repo = (content.get("repository") or {}).get("nameWithOwner") or "(unknown repo)"
        issues.append(
            ReleaseIssue(
                item_id=item["id"],
                title=content["title"],
                number=content["number"],
                url=content["url"],
                repository=repo,
                state=content["state"],
                status=values.get(status_field_name),
                release_status=values.get(release_status_field_name),
            )
        )

    issues.sort(key=lambda issue: (issue.repository, issue.number))
    skipped.sort()
    return issues, skipped


def print_issues(title: str, issues: list[ReleaseIssue]) -> None:
    log_group(f"{title} ({len(issues)})")
    if not issues:
        print("(none)")
    for issue in issues:
        print(
            f"- {issue.repository}#{issue.number} {issue.url} "
            f"Status: {issue.status or '(empty)'} "
            f"Release Status: {issue.release_status or '(empty)'} "
            f"Title: {issue.title}"
        )
    end_group()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--project-owner", default="sima-neat")
    parser.add_argument("--project-number", required=True, type=int)
    parser.add_argument("--release-field-name", default="Target Release")
    parser.add_argument("--status-field-name", default="Status")
    parser.add_argument("--done-status-value", default="Done")
    parser.add_argument("--release-status-field-name", default="Release Status")
    parser.add_argument("--released-status-value", default="released")
    parser.add_argument("--allow-empty-release", default=False, type=parse_bool)
    parser.add_argument("--dry-run", default=False, type=parse_bool)
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        die("GH_TOKEN or GITHUB_TOKEN is required")

    client = GitHubGraphQL(token)

    project, fields = get_project(client, args.project_owner, args.project_number)
    print(f"Project: {project['title']} {project['url']}")
    print(f"Release version: {args.release_version}")
    print(f"Dry run: {args.dry_run}")

    require_field(fields, args.release_field_name)
    require_field(fields, args.status_field_name)
    release_status_field = require_field(fields, args.release_status_field_name)
    released_option_id = require_single_select_option(release_status_field, args.released_status_value)

    items = get_items(client, project["id"])
    issues, skipped = collect_release_issues(
        items=items,
        release_version=args.release_version,
        release_field_name=args.release_field_name,
        status_field_name=args.status_field_name,
        release_status_field_name=args.release_status_field_name,
    )

    if skipped:
        log_group(f"Skipped non-issue project items ({len(skipped)})")
        for line in skipped:
            print(f"- {line}")
        end_group()

    done_issues = [issue for issue in issues if issue.status == args.done_status_value]
    blocking_issues = [issue for issue in issues if issue.status != args.done_status_value]

    print_issues("Issues targeted for release", issues)
    print_issues("Issues already Done", done_issues)
    print_issues("Issues blocking release", blocking_issues)

    if not issues and not args.allow_empty_release:
        die(
            f"no GitHub issues found in project {project['title']} with "
            f"{args.release_field_name}={args.release_version}"
        )

    if blocking_issues:
        print(
            f"Release issue validation failed for {args.release_version}. "
            "No release branch, tag, or draft release should be created.",
            file=sys.stderr,
        )
        return 1

    print(f"All {len(issues)} issue(s) targeted for {args.release_version} are {args.done_status_value}.")

    log_group("Mark released project items")
    for issue in issues:
        if issue.release_status == args.released_status_value:
            print(f"- already {args.released_status_value}: {issue.repository}#{issue.number} {issue.url}")
            continue
        if args.dry_run:
            print(f"- dry-run update: {issue.repository}#{issue.number} {issue.url}")
            continue
        client.request(
            UPDATE_FIELD_MUTATION,
            {
                "projectId": project["id"],
                "itemId": issue.item_id,
                "fieldId": release_status_field.id,
                "optionId": released_option_id,
            },
        )
        print(f"- updated: {issue.repository}#{issue.number} {issue.url}")
    end_group()

    print(f"Release issue validation passed for {args.release_version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
