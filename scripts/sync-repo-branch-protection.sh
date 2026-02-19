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

POLICY_FILE="${POLICY_FILE:-policies/repo-branch-protection/config.json}"
API_BASE="https://api.github.com"
API_VERSION="2022-11-28"
PER_PAGE=100

if [[ ! -f "${POLICY_FILE}" ]]; then
  echo "Policy file not found: ${POLICY_FILE}"
  exit 1
fi

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

list_release_branches() {
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
    jq -r '.[] | .name' <<<"$resp" | awk '/^release-.+/'
    page=$((page + 1))
  done
}

protect_branch() {
  local repo="$1"
  local branch="$2"
  local protection_payload="$3"
  local encoded_branch
  local url
  local headers_file
  local body_file
  local status
  encoded_branch="$(jq -rn --arg b "$branch" '$b|@uri')"
  url="${API_BASE}/repos/${ORG_NAME}/${repo}/branches/${encoded_branch}/protection"
  headers_file="$(mktemp)"
  body_file="$(mktemp)"

  echo "Applying protection: ${repo}:${branch}"
  status="$(
    curl -sS \
      -X PUT \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer ${API_TOKEN}" \
      -H "X-GitHub-Api-Version: ${API_VERSION}" \
      -D "$headers_file" \
      -o "$body_file" \
      -w "%{http_code}" \
      "$url" \
      -d "$protection_payload"
  )"

  if [[ "$status" == "403" ]] && grep -q "Upgrade to GitHub Pro or make this repository public to enable this feature." "$body_file"; then
    echo "Skipping ${repo}:${branch} (branch protection unavailable for private repos on current GitHub plan)"
    rm -f "$headers_file" "$body_file"
    return 0
  fi

  if (( status >= 400 )); then
    echo "GitHub API request failed: PUT ${url} -> HTTP ${status}" >&2
    if grep -qi '^x-accepted-github-permissions:' "$headers_file"; then
      echo "Accepted permissions:" >&2
      grep -i '^x-accepted-github-permissions:' "$headers_file" >&2
    fi
    echo "Response body:" >&2
    cat "$body_file" >&2
    rm -f "$headers_file" "$body_file"
    return 1
  fi

  rm -f "$headers_file" "$body_file"
}

main_branch="$(jq -r '.main_branch' "${POLICY_FILE}")"
main_payload="$(jq -c '.main_protection' "${POLICY_FILE}")"
release_payload="$(jq -c '.release_protection' "${POLICY_FILE}")"

echo "Fetching repositories for org ${ORG_NAME}..."
mapfile -t repos < <(list_repos)
echo "Found ${#repos[@]} active repositories."

for repo in "${repos[@]}"; do
  echo "--- ${repo}"

  if branch_exists "$repo" "$main_branch"; then
    protect_branch "$repo" "$main_branch" "$main_payload"
  else
    echo "Skipping ${repo}:${main_branch} (branch does not exist)"
  fi

  mapfile -t release_branches < <(list_release_branches "$repo" | sort -u)
  if [[ "${#release_branches[@]}" -eq 0 ]]; then
    echo "No release-* branches found."
    continue
  fi

  for branch in "${release_branches[@]}"; do
    protect_branch "$repo" "$branch" "$release_payload"
  done
done

echo "Repository branch protection sync complete."
