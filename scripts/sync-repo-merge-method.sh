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

POLICY_FILE="${POLICY_FILE:-policies/repo-merge-method/config.json}"
API_BASE="https://api.github.com"
API_VERSION="2022-11-28"
PER_PAGE=100
DRY_RUN="${DRY_RUN:-false}"

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

validate_boolean_setting() {
  local payload="$1"
  local key="$2"
  local type
  type="$(jq -r --arg key "$key" '.[$key] | type' <<<"$payload")"
  if [[ "$type" != "boolean" ]]; then
    echo "Invalid setting for ${key}: expected boolean, got ${type}" >&2
    exit 1
  fi
}

build_repo_payload() {
  local repo="$1"
  jq -c \
    --arg repo "$repo" \
    '
      (.defaults // {}) + (.repos[$repo] // {})
      | {
          allow_squash_merge,
          allow_merge_commit,
          allow_rebase_merge
        }
    ' "$POLICY_FILE"
}

sync_repo_settings() {
  local repo="$1"
  local payload="$2"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "DRY_RUN ${repo} -> ${payload}"
    return 0
  fi

  echo "Applying merge-method settings: ${repo}"
  api PATCH "${API_BASE}/repos/${ORG_NAME}/${repo}" "$payload" >/dev/null
}

exclude_repos_json="$(jq -c '.exclude_repos // []' "$POLICY_FILE")"
target_repos=()
while IFS= read -r repo; do
  [[ -z "$repo" ]] && continue
  target_repos+=("$repo")
done < <(jq -r '.target_repos[]?' "$POLICY_FILE")

repos=()
if [[ "${#target_repos[@]}" -gt 0 ]]; then
  repos=("${target_repos[@]}")
else
  echo "Fetching repositories for org ${ORG_NAME}..."
  while IFS= read -r repo; do
    [[ -z "$repo" ]] && continue
    repos+=("$repo")
  done < <(list_repos)
  echo "Found ${#repos[@]} active repositories."
fi

if [[ "${#repos[@]}" -eq 0 ]]; then
  echo "No repositories selected for sync."
  exit 0
fi

for repo in "${repos[@]}"; do
  if jq -e --arg repo "$repo" '.[] | select(. == $repo)' <<<"$exclude_repos_json" >/dev/null; then
    echo "--- ${repo} (excluded)"
    continue
  fi

  payload="$(build_repo_payload "$repo")"
  validate_boolean_setting "$payload" "allow_squash_merge"
  validate_boolean_setting "$payload" "allow_merge_commit"
  validate_boolean_setting "$payload" "allow_rebase_merge"

  echo "--- ${repo}"
  echo "  squash=$(jq -r '.allow_squash_merge' <<<"$payload") merge_commit=$(jq -r '.allow_merge_commit' <<<"$payload") rebase=$(jq -r '.allow_rebase_merge' <<<"$payload")"
  sync_repo_settings "$repo" "$payload"
done

echo "Repository merge-method sync complete."
