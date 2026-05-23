#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${ORG_NAME:-}" ]]; then
  echo "ORG_NAME is required"
  exit 1
fi

API_TOKEN="${ORG_ADMIN_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ -z "${API_TOKEN}" ]]; then
  echo "Token is required. Set ORG_ADMIN_TOKEN or GITHUB_TOKEN."
  exit 1
fi

POLICY_FILE="${POLICY_FILE:-policies/repo-codeowners/config.json}"
API_BASE="https://api.github.com"
API_VERSION="2022-11-28"
PER_PAGE=100

if [[ ! -f "${POLICY_FILE}" ]]; then
  echo "Policy file not found: ${POLICY_FILE}"
  exit 1
fi

for cmd in base64 curl jq python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd"
    exit 1
  fi
done

api() {
  local method="$1"
  local url="$2"
  local data="${3:-}"
  local headers_file
  local body_file
  local status
  headers_file="$(mktemp)"
  body_file="$(mktemp)"

  if [[ -n "$data" ]]; then
    status="$(
      curl -sS \
        -X "$method" \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer ${API_TOKEN}" \
        -H "X-GitHub-Api-Version: ${API_VERSION}" \
        -D "$headers_file" \
        -o "$body_file" \
        -w "%{http_code}" \
        "$url" \
        -d "$data"
    )"
  else
    status="$(
      curl -sS \
        -X "$method" \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer ${API_TOKEN}" \
        -H "X-GitHub-Api-Version: ${API_VERSION}" \
        -D "$headers_file" \
        -o "$body_file" \
        -w "%{http_code}" \
        "$url"
    )"
  fi

  if (( status >= 400 )); then
    echo "GitHub API request failed: ${method} ${url} -> HTTP ${status}" >&2
    if grep -qi '^x-accepted-github-permissions:' "$headers_file"; then
      echo "Accepted permissions:" >&2
      grep -i '^x-accepted-github-permissions:' "$headers_file" >&2
    fi
    echo "Response body:" >&2
    cat "$body_file" >&2
    rm -f "$headers_file" "$body_file"
    return 1
  fi

  cat "$body_file"
  rm -f "$headers_file" "$body_file"
}

api_to_file() {
  local method="$1"
  local url="$2"
  local output_file="$3"
  local data="${4:-}"
  local headers_file
  local status
  headers_file="$(mktemp)"

  if [[ -n "$data" ]]; then
    status="$(
      curl -sS \
        -X "$method" \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer ${API_TOKEN}" \
        -H "X-GitHub-Api-Version: ${API_VERSION}" \
        -D "$headers_file" \
        -o "$output_file" \
        -w "%{http_code}" \
        "$url" \
        -d "$data"
    )"
  else
    status="$(
      curl -sS \
        -X "$method" \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer ${API_TOKEN}" \
        -H "X-GitHub-Api-Version: ${API_VERSION}" \
        -D "$headers_file" \
        -o "$output_file" \
        -w "%{http_code}" \
        "$url"
    )"
  fi

  if [[ "$status" == "404" ]]; then
    rm -f "$headers_file"
    return 2
  fi

  if (( status >= 400 )); then
    echo "GitHub API request failed: ${method} ${url} -> HTTP ${status}" >&2
    if grep -qi '^x-accepted-github-permissions:' "$headers_file"; then
      echo "Accepted permissions:" >&2
      grep -i '^x-accepted-github-permissions:' "$headers_file" >&2
    fi
    echo "Response body:" >&2
    cat "$output_file" >&2
    rm -f "$headers_file"
    return 1
  fi

  rm -f "$headers_file"
}

branch_exists() {
  local repo="$1"
  local branch="$2"
  local code
  code="$(
    curl -sS \
      -o /dev/null \
      -w "%{http_code}" \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer ${API_TOKEN}" \
      -H "X-GitHub-Api-Version: ${API_VERSION}" \
      "${API_BASE}/repos/${ORG_NAME}/${repo}/branches/${branch}"
  )"
  [[ "$code" == "200" ]]
}

list_repos() {
  local page=1
  while true; do
    local url="${API_BASE}/orgs/${ORG_NAME}/repos?type=all&per_page=${PER_PAGE}&page=${page}"
    local resp
    if ! resp="$(api GET "$url")"; then
      return 1
    fi

    local count
    count="$(jq 'length' <<<"$resp")"
    if [[ "$count" == "0" ]]; then
      break
    fi

    jq -r '.[] | select((.archived | not) and (.disabled | not)) | .name' <<<"$resp"
    page=$((page + 1))
  done
}

ensure_team() {
  local team_name="$1"
  local team_slug="$2"
  local team_description="$3"
  local team_privacy="$4"
  local team_file
  local url
  team_file="$(mktemp)"
  url="${API_BASE}/orgs/${ORG_NAME}/teams/${team_slug}"

  if api_to_file GET "$url" "$team_file"; then
    current_privacy="$(jq -r '.privacy' "$team_file")"
    if [[ "$current_privacy" != "$team_privacy" ]]; then
      echo "Updating team privacy: ${team_slug} ${current_privacy} -> ${team_privacy}"
      api PATCH "$url" "$(jq -cn \
        --arg name "$team_name" \
        --arg description "$team_description" \
        --arg privacy "$team_privacy" \
        '{name: $name, description: $description, privacy: $privacy}')" >/dev/null
    else
      echo "Team exists: ${team_slug}"
    fi
  else
    status="$?"
    if [[ "$status" != "2" ]]; then
      rm -f "$team_file"
      return 1
    fi
    echo "Creating team: ${team_slug}"
    api POST "${API_BASE}/orgs/${ORG_NAME}/teams" "$(jq -cn \
      --arg name "$team_name" \
      --arg description "$team_description" \
      --arg privacy "$team_privacy" \
      '{name: $name, description: $description, privacy: $privacy}')" >/dev/null
  fi

  rm -f "$team_file"
}

build_codeowners() {
  local input_file="$1"
  local output_file="$2"
  local owner="$3"
  local start_marker="$4"
  local end_marker="$5"

  python3 - "$input_file" "$output_file" "$owner" "$start_marker" "$end_marker" <<'PY'
from pathlib import Path
import sys

input_path, output_path, owner, start_marker, end_marker = sys.argv[1:]
existing = Path(input_path).read_text() if Path(input_path).exists() else ""
block = "\n".join([
    start_marker,
    "# Managed by sima-neat/.github policies/repo-codeowners/config.json",
    f"* {owner}",
    end_marker,
])

def add_owner_to_default_line(line: str) -> str:
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#"):
        return line

    body, sep, comment = line.partition(" #")
    tokens = body.split()
    if not tokens or tokens[0] not in {"*", "/*"} or owner in tokens[1:]:
        return line

    return body.rstrip() + f" {owner}" + (sep + comment if sep else "")

def replace_managed_block(lines: list[str]) -> list[str]:
    try:
        start = lines.index(start_marker)
        end = lines.index(end_marker, start + 1)
    except ValueError:
        return []
    return lines[:start] + block.splitlines() + lines[end + 1:]

lines = existing.splitlines()
if not existing.strip():
    result = block.splitlines()
else:
    replaced = replace_managed_block(lines)
    unmanaged = replaced if replaced else block.splitlines() + [""] + lines
    result = [add_owner_to_default_line(line) for line in unmanaged]

Path(output_path).write_text("\n".join(result).rstrip() + "\n")
PY
}

read_policy_array() {
  local jq_expr="$1"
  local output_file="$2"
  jq -r "$jq_expr" "$POLICY_FILE" >"$output_file"
}

team_name="$(jq -r '.team.name // empty' "$POLICY_FILE")"
team_slug="$(jq -r '.team.slug // empty' "$POLICY_FILE")"
team_description="$(jq -r '.team.description // ""' "$POLICY_FILE")"
team_privacy="$(jq -r '.team.privacy // "closed"' "$POLICY_FILE")"
team_permission="$(jq -r '.team.permission // "push"' "$POLICY_FILE")"
codeowners_path="$(jq -r '.codeowners_path // ".github/CODEOWNERS"' "$POLICY_FILE")"
start_marker="$(jq -r '.managed_block.start // empty' "$POLICY_FILE")"
end_marker="$(jq -r '.managed_block.end // empty' "$POLICY_FILE")"
commit_message="$(jq -r '.commit_message // "chore: sync CODEOWNERS"' "$POLICY_FILE")"

if [[ -z "$team_name" || -z "$team_slug" || -z "$start_marker" || -z "$end_marker" ]]; then
  echo "Policy file must define team.name, team.slug, managed_block.start, and managed_block.end"
  exit 1
fi

if [[ "$team_privacy" != "closed" ]]; then
  echo "Team privacy must be 'closed' so GitHub can use it as a visible CODEOWNERS team"
  exit 1
fi

if [[ "$team_permission" != "push" && "$team_permission" != "maintain" && "$team_permission" != "admin" ]]; then
  echo "Team permission must give write access: push, maintain, or admin"
  exit 1
fi

owner="@${ORG_NAME}/${team_slug}"
encoded_path="$(jq -rn --arg path "$codeowners_path" '$path | @uri')"

branches_file="$(mktemp)"
exclude_repos_file="$(mktemp)"
repos_file="$(mktemp)"
read_policy_array '.branches[]?' "$branches_file"
read_policy_array '.exclude_repos[]?' "$exclude_repos_file"

if [[ ! -s "$branches_file" ]]; then
  echo "Policy file must define at least one branch"
  rm -f "$branches_file" "$exclude_repos_file" "$repos_file"
  exit 1
fi

ensure_team "$team_name" "$team_slug" "$team_description" "$team_privacy"

echo "Fetching repositories for org ${ORG_NAME}..."
if ! list_repos >"$repos_file"; then
  rm -f "$branches_file" "$exclude_repos_file" "$repos_file"
  exit 1
fi

repo_count="$(wc -l <"$repos_file" | tr -d ' ')"
echo "Found ${repo_count} active repositories."

while IFS= read -r repo; do
  [[ -z "$repo" ]] && continue
  if grep -Fxq "$repo" "$exclude_repos_file"; then
    echo "--- ${repo} (excluded)"
    continue
  fi

  echo "--- ${repo}"
  echo "Ensuring team write access: ${team_slug} -> ${team_permission}"
  api PUT "${API_BASE}/orgs/${ORG_NAME}/teams/${team_slug}/repos/${ORG_NAME}/${repo}" "$(jq -cn \
    --arg permission "$team_permission" \
    '{permission: $permission}')" >/dev/null

  while IFS= read -r branch; do
    [[ -z "$branch" ]] && continue
    if ! branch_exists "$repo" "$branch"; then
      echo "Skipping ${repo}:${branch} (branch does not exist)"
      continue
    fi

    response_file="$(mktemp)"
    existing_file="$(mktemp)"
    desired_file="$(mktemp)"
    sha=""

    if api_to_file GET "${API_BASE}/repos/${ORG_NAME}/${repo}/contents/${encoded_path}?ref=${branch}" "$response_file"; then
      jq -r '.content | gsub("\n"; "") | @base64d' "$response_file" >"$existing_file"
      sha="$(jq -r '.sha' "$response_file")"
    else
      status="$?"
      if [[ "$status" != "2" ]]; then
        rm -f "$response_file" "$existing_file" "$desired_file"
        exit 1
      fi
      : >"$existing_file"
    fi

    build_codeowners "$existing_file" "$desired_file" "$owner" "$start_marker" "$end_marker"

    if cmp -s "$existing_file" "$desired_file"; then
      echo "No change: ${repo}:${branch}:${codeowners_path}"
      rm -f "$response_file" "$existing_file" "$desired_file"
      continue
    fi

    encoded_content="$(base64 <"$desired_file" | tr -d '\n')"
    if [[ -n "$sha" ]]; then
      payload="$(jq -cn \
        --arg message "$commit_message" \
        --arg content "$encoded_content" \
        --arg branch "$branch" \
        --arg sha "$sha" \
        '{message: $message, content: $content, branch: $branch, sha: $sha}')"
    else
      payload="$(jq -cn \
        --arg message "$commit_message" \
        --arg content "$encoded_content" \
        --arg branch "$branch" \
        '{message: $message, content: $content, branch: $branch}')"
    fi

    echo "Updating ${repo}:${branch}:${codeowners_path}"
    api PUT "${API_BASE}/repos/${ORG_NAME}/${repo}/contents/${encoded_path}" "$payload" >/dev/null
    rm -f "$response_file" "$existing_file" "$desired_file"
  done <"$branches_file"
done <"$repos_file"

rm -f "$branches_file" "$exclude_repos_file" "$repos_file"
echo "Repo CODEOWNERS sync complete."
