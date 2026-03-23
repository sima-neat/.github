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

POLICY_FILE="${POLICY_FILE:-policies/repo-default-branch/config.json}"
API_BASE="https://api.github.com"
API_VERSION="2022-11-28"
PER_PAGE=100

if [[ ! -f "${POLICY_FILE}" ]]; then
  echo "Policy file not found: ${POLICY_FILE}"
  exit 1
fi

for cmd in curl jq; do
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

list_repos() {
  local page=1
  while true; do
    local url="${API_BASE}/orgs/${ORG_NAME}/repos?type=all&per_page=${PER_PAGE}&page=${page}"
    local resp
    resp="$(api GET "$url")"
    local count
    count="$(jq 'length' <<<"$resp")"
    if [[ "$count" == "0" ]]; then
      break
    fi
    jq -r '.[] | select((.archived | not) and (.disabled | not)) | .name' <<<"$resp"
    page=$((page + 1))
  done
}

list_repo_branches() {
  local repo="$1"
  local page=1
  while true; do
    local url="${API_BASE}/repos/${ORG_NAME}/${repo}/branches?per_page=${PER_PAGE}&page=${page}"
    local resp
    resp="$(api GET "$url")"
    local count
    count="$(jq 'length' <<<"$resp")"
    if [[ "$count" == "0" ]]; then
      break
    fi
    jq -r '.[].name' <<<"$resp"
    page=$((page + 1))
  done
}

repo_default_branch() {
  local repo="$1"
  local url="${API_BASE}/repos/${ORG_NAME}/${repo}"
  local resp
  resp="$(api GET "$url")"
  jq -r '.default_branch' <<<"$resp"
}

set_default_branch() {
  local repo="$1"
  local branch="$2"
  local payload
  payload="$(jq -cn --arg b "$branch" '{default_branch: $b}')"

  echo "Setting default branch for ${repo} -> ${branch}"
  api PATCH "${API_BASE}/repos/${ORG_NAME}/${repo}" "$payload" >/dev/null
}

exclude_repos_json="$(jq -c '.exclude_repos // []' "$POLICY_FILE")"
mapfile -t branch_priority < <(jq -r '.branch_priority[]?' "$POLICY_FILE")

if [[ "${#branch_priority[@]}" -eq 0 ]]; then
  echo "No branch_priority configured in ${POLICY_FILE}"
  exit 1
fi

echo "Fetching repositories for org ${ORG_NAME}..."
mapfile -t repos < <(list_repos)
echo "Found ${#repos[@]} active repositories."

for repo in "${repos[@]}"; do
  if jq -e --arg repo "$repo" '.[] | select(. == $repo)' <<<"$exclude_repos_json" >/dev/null; then
    echo "--- ${repo} (excluded)"
    continue
  fi

  echo "--- ${repo}"
  mapfile -t existing_branches < <(list_repo_branches "$repo" | sort -u)

  target_branch=""
  for preferred in "${branch_priority[@]}"; do
    if printf '%s\n' "${existing_branches[@]}" | grep -Fxq "$preferred"; then
      target_branch="$preferred"
      break
    fi
  done

  if [[ -z "$target_branch" ]]; then
    echo "Skipping ${repo}: none of the preferred branches exist (${branch_priority[*]})"
    continue
  fi

  current_default="$(repo_default_branch "$repo")"
  if [[ "$current_default" == "$target_branch" ]]; then
    echo "No change: default branch is already ${current_default}"
    continue
  fi

  set_default_branch "$repo" "$target_branch"
done

echo "Repository default branch sync complete."
