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

POLICY_FILE="${POLICY_FILE:-policies/repo-permissions/config.json}"
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

api_allow_404() {
  local method="$1"
  local url="$2"
  local data="${3:-}"
  local status
  local body_file
  body_file="$(mktemp)"

  if [[ -n "$data" ]]; then
    status="$(
      curl -sS \
        -X "$method" \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer ${API_TOKEN}" \
        -H "X-GitHub-Api-Version: ${API_VERSION}" \
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
        -o "$body_file" \
        -w "%{http_code}" \
        "$url"
    )"
  fi

  if [[ "$status" == "404" ]]; then
    rm -f "$body_file"
    return 0
  fi

  if (( status >= 400 )); then
    echo "GitHub API request failed: ${method} ${url} -> HTTP ${status}" >&2
    echo "Response body:" >&2
    cat "$body_file" >&2
    rm -f "$body_file"
    return 1
  fi

  rm -f "$body_file"
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

list_repo_team_slugs() {
  local repo="$1"
  local page=1
  while true; do
    local url="${API_BASE}/repos/${ORG_NAME}/${repo}/teams?per_page=${PER_PAGE}&page=${page}"
    local resp
    resp="$(api GET "$url")"
    local count
    count="$(jq 'length' <<<"$resp")"
    if [[ "$count" == "0" ]]; then
      break
    fi
    jq -r '.[].slug' <<<"$resp"
    page=$((page + 1))
  done
}

list_repo_direct_users() {
  local repo="$1"
  local page=1
  while true; do
    local url="${API_BASE}/repos/${ORG_NAME}/${repo}/collaborators?affiliation=direct&per_page=${PER_PAGE}&page=${page}"
    local resp
    resp="$(api GET "$url")"
    local count
    count="$(jq 'length' <<<"$resp")"
    if [[ "$count" == "0" ]]; then
      break
    fi
    jq -r '.[].login' <<<"$resp"
    page=$((page + 1))
  done
}

array_contains() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    if [[ "$item" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

normalize_permission() {
  local value="$1"
  case "${value,,}" in
    pull|read) echo "pull" ;;
    push|write) echo "push" ;;
    triage) echo "triage" ;;
    maintain|maintainer) echo "maintain" ;;
    admin) echo "admin" ;;
    *)
      echo "invalid"
      ;;
  esac
}

exclude_repos_json="$(jq -c '.exclude_repos // []' "$POLICY_FILE")"
prune_teams="$(jq -r '.prune_unmanaged.teams // false' "$POLICY_FILE")"
prune_users="$(jq -r '.prune_unmanaged.users // false' "$POLICY_FILE")"

echo "Fetching repositories for org ${ORG_NAME}..."
mapfile -t repos < <(list_repos)
echo "Found ${#repos[@]} active repositories."

for repo in "${repos[@]}"; do
  if jq -e --arg repo "$repo" '.[] | select(. == $repo)' <<<"$exclude_repos_json" >/dev/null; then
    echo "--- ${repo} (excluded)"
    continue
  fi

  echo "--- ${repo}"
  inherit_defaults="$(jq -r --arg repo "$repo" '.repos[$repo].inherit_defaults // true' "$POLICY_FILE")"

  if [[ "$inherit_defaults" == "true" ]]; then
    teams_json="$(jq -c --arg repo "$repo" '((.defaults.teams // []) + (.repos[$repo].teams // [])) | unique_by(.slug)' "$POLICY_FILE")"
    users_json="$(jq -c --arg repo "$repo" '((.defaults.users // []) + (.repos[$repo].users // [])) | unique_by(.username)' "$POLICY_FILE")"
  else
    teams_json="$(jq -c --arg repo "$repo" '(.repos[$repo].teams // []) | unique_by(.slug)' "$POLICY_FILE")"
    users_json="$(jq -c --arg repo "$repo" '(.repos[$repo].users // []) | unique_by(.username)' "$POLICY_FILE")"
  fi

  mapfile -t managed_team_slugs < <(jq -r '.[].slug' <<<"$teams_json")
  mapfile -t managed_usernames < <(jq -r '.[].username' <<<"$users_json")

  while IFS= read -r item; do
    [[ -z "$item" ]] && continue
    team_slug="$(jq -r '.slug' <<<"$item")"
    permission_raw="$(jq -r '.permission' <<<"$item")"
    permission="$(normalize_permission "$permission_raw")"
    if [[ "$permission" == "invalid" ]]; then
      echo "Invalid team permission '${permission_raw}' for team '${team_slug}' in repo '${repo}'"
      exit 1
    fi
    echo "Ensuring team permission: ${team_slug} -> ${permission}"
    api PUT "${API_BASE}/orgs/${ORG_NAME}/teams/${team_slug}/repos/${ORG_NAME}/${repo}" "{\"permission\":\"${permission}\"}" >/dev/null
  done < <(jq -c '.[]' <<<"$teams_json")

  while IFS= read -r item; do
    [[ -z "$item" ]] && continue
    username="$(jq -r '.username' <<<"$item")"
    permission_raw="$(jq -r '.permission' <<<"$item")"
    permission="$(normalize_permission "$permission_raw")"
    if [[ "$permission" == "invalid" ]]; then
      echo "Invalid user permission '${permission_raw}' for user '${username}' in repo '${repo}'"
      exit 1
    fi
    echo "Ensuring user permission: ${username} -> ${permission}"
    api PUT "${API_BASE}/repos/${ORG_NAME}/${repo}/collaborators/${username}" "{\"permission\":\"${permission}\"}" >/dev/null
  done < <(jq -c '.[]' <<<"$users_json")

  if [[ "$prune_teams" == "true" ]]; then
    echo "Pruning unmanaged team permissions..."
    mapfile -t current_team_slugs < <(list_repo_team_slugs "$repo" | sort -u)
    for slug in "${current_team_slugs[@]}"; do
      if ! array_contains "$slug" "${managed_team_slugs[@]}"; then
        echo "Removing unmanaged team: ${slug}"
        api_allow_404 DELETE "${API_BASE}/orgs/${ORG_NAME}/teams/${slug}/repos/${ORG_NAME}/${repo}"
      fi
    done
  fi

  if [[ "$prune_users" == "true" ]]; then
    echo "Pruning unmanaged direct collaborators..."
    mapfile -t current_users < <(list_repo_direct_users "$repo" | sort -u)
    for user in "${current_users[@]}"; do
      if ! array_contains "$user" "${managed_usernames[@]}"; then
        echo "Removing unmanaged collaborator: ${user}"
        api_allow_404 DELETE "${API_BASE}/repos/${ORG_NAME}/${repo}/collaborators/${user}"
      fi
    done
  fi
done

echo "Repository permission sync complete."
