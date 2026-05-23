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

POLICY_FILE="${POLICY_FILE:-policies/org-rulesets/protect-main-develop.json}"
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

list_rulesets() {
  local page=1
  while true; do
    local url="${API_BASE}/orgs/${ORG_NAME}/rulesets?per_page=${PER_PAGE}&page=${page}"
    local resp
    if ! resp="$(api GET "$url")"; then
      return 1
    fi

    local count
    count="$(jq 'length' <<<"$resp")"
    if [[ "$count" == "0" ]]; then
      break
    fi

    jq -c '.[]' <<<"$resp"
    page=$((page + 1))
  done
}

verify_ruleset() {
  local ruleset_id="$1"
  local expected_payload="$2"
  local actual
  actual="$(api GET "${API_BASE}/orgs/${ORG_NAME}/rulesets/${ruleset_id}")"

  jq -e --argjson expected "$expected_payload" '
    .name == $expected.name and
    .target == $expected.target and
    .enforcement == $expected.enforcement and
    .conditions.repository_name.include == $expected.conditions.repository_name.include and
    .conditions.repository_name.exclude == $expected.conditions.repository_name.exclude and
    .conditions.repository_name.protected == $expected.conditions.repository_name.protected and
    .conditions.ref_name.include == $expected.conditions.ref_name.include and
    .conditions.ref_name.exclude == $expected.conditions.ref_name.exclude and
    ([.rules[].type] | sort) == ([$expected.rules[].type] | sort) and
    (
      .rules[] | select(.type == "pull_request") | .parameters as $actual_pr |
      $expected.rules[] | select(.type == "pull_request") | .parameters as $expected_pr |
      $actual_pr.dismiss_stale_reviews_on_push == $expected_pr.dismiss_stale_reviews_on_push and
      $actual_pr.require_code_owner_review == $expected_pr.require_code_owner_review and
      $actual_pr.require_last_push_approval == $expected_pr.require_last_push_approval and
      $actual_pr.required_approving_review_count == $expected_pr.required_approving_review_count and
      $actual_pr.required_review_thread_resolution == $expected_pr.required_review_thread_resolution
    )
  ' <<<"$actual" >/dev/null
}

payload="$(jq -c '.' "$POLICY_FILE")"
ruleset_name="$(jq -r '.name // empty' "$POLICY_FILE")"
if [[ -z "$ruleset_name" ]]; then
  echo "Policy file must define .name"
  exit 1
fi

rulesets_file="$(mktemp)"
if ! list_rulesets >"$rulesets_file"; then
  rm -f "$rulesets_file"
  exit 1
fi

match_count="$(
  jq -s --arg name "$ruleset_name" \
    '[.[] | select(.name == $name and .target == "branch")] | length' \
    "$rulesets_file"
)"

if (( match_count > 1 )); then
  echo "Found ${match_count} branch rulesets named '${ruleset_name}'. Refusing to choose one."
  rm -f "$rulesets_file"
  exit 1
fi

if (( match_count == 0 )); then
  echo "Creating org ruleset: ${ruleset_name}"
  ruleset_id="$(api POST "${API_BASE}/orgs/${ORG_NAME}/rulesets" "$payload" | jq -r '.id')"
else
  ruleset_id="$(
    jq -r --arg name "$ruleset_name" \
      'select(.name == $name and .target == "branch") | .id' \
      "$rulesets_file"
  )"
  echo "Updating org ruleset: ${ruleset_name} (${ruleset_id})"
  api PUT "${API_BASE}/orgs/${ORG_NAME}/rulesets/${ruleset_id}" "$payload" >/dev/null
fi

rm -f "$rulesets_file"

verify_ruleset "$ruleset_id" "$payload"
echo "Org ruleset sync complete: ${ruleset_name} (${ruleset_id})"
