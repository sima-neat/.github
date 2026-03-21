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

POLICY_FILE="${POLICY_FILE:-policies/repo-environments/config.json}"
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
  local public_only="$1"
  while true; do
    local url="${API_BASE}/orgs/${ORG_NAME}/repos?type=all&per_page=${PER_PAGE}&page=${page}"
    local resp
    resp="$(api GET "$url")"
    local count
    count="$(jq 'length' <<<"$resp")"
    if [[ "$count" == "0" ]]; then
      break
    fi

    if [[ "$public_only" == "true" ]]; then
      jq -r '.[] | select((.archived | not) and (.disabled | not) and (.private | not)) | .name' <<<"$resp"
    else
      jq -r '.[] | select((.archived | not) and (.disabled | not)) | .name' <<<"$resp"
    fi

    page=$((page + 1))
  done
}

create_or_update_environment() {
  local repo="$1"
  local env_name="$2"
  local payload="$3"
  local encoded_name
  encoded_name="$(jq -rn --arg v "$env_name" '$v|@uri')"

  echo "Ensuring environment '${env_name}' on ${repo}"
  api PUT "${API_BASE}/repos/${ORG_NAME}/${repo}/environments/${encoded_name}" "$payload" >/dev/null
}

exclude_repos_json="$(jq -c '.exclude_repos // []' "$POLICY_FILE")"
public_repos_only="$(jq -r '.public_repos_only // true' "$POLICY_FILE")"
env_count="$(jq '.environments | length' "$POLICY_FILE")"
if [[ "$env_count" == "0" ]]; then
  echo "No environments configured in ${POLICY_FILE}"
  exit 1
fi

echo "Fetching repositories for org ${ORG_NAME} (public_only=${public_repos_only})..."
mapfile -t repos < <(list_repos "$public_repos_only")
echo "Found ${#repos[@]} managed repositories."

for repo in "${repos[@]}"; do
  if jq -e --arg repo "$repo" '.[] | select(. == $repo)' <<<"$exclude_repos_json" >/dev/null; then
    echo "--- ${repo} (excluded)"
    continue
  fi

  echo "--- ${repo}"

  while IFS= read -r item; do
    [[ -z "$item" ]] && continue
    env_name="$(jq -r '.name // empty' <<<"$item")"
    if [[ -z "$env_name" ]]; then
      echo "Environment entry missing name in ${POLICY_FILE}" >&2
      exit 1
    fi

    protected_branches="$(jq -r '.deployment_branch_policy.protected_branches // true' <<<"$item")"
    custom_branch_policies="$(jq -r '.deployment_branch_policy.custom_branch_policies // false' <<<"$item")"

    payload="$(jq -cn \
      --argjson protected "$protected_branches" \
      --argjson custom "$custom_branch_policies" \
      '{deployment_branch_policy: {protected_branches: $protected, custom_branch_policies: $custom}}')"

    create_or_update_environment "$repo" "$env_name" "$payload"
  done < <(jq -c '.environments[]' "$POLICY_FILE")
done

echo "Repository environment sync complete."
