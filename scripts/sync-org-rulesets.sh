#!/usr/bin/env bash
set -euo pipefail

CHECK_ACCESS=false
if [[ $# -eq 1 && "$1" == "--check-access" ]]; then
  CHECK_ACCESS=true
elif [[ $# -ne 0 ]]; then
  echo "Usage: $0 [--check-access]" >&2
  exit 1
fi

if [[ -z "${ORG_NAME:-}" ]]; then
  echo "ORG_NAME is required"
  exit 1
fi

API_TOKEN="${GH_TOKEN:-}"
if [[ -z "${API_TOKEN}" ]]; then
  echo "Set GH_TOKEN to a GitHub App installation token with organization Administration write and Members read."
  exit 1
fi

POLICY_DIR="${POLICY_DIR:-policies/org-rulesets}"
POLICY_FILE="${POLICY_FILE:-}"
API_BASE="https://api.github.com"
API_VERSION="2022-11-28"
PER_PAGE=100
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ACCESS_POLICY_FILE="${ACCESS_POLICY_FILE:-policies/access/config.json}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
RENAME_FILE="${POLICY_DIR}/migrations/renames.json"

if [[ -n "${POLICY_FILE}" && ! -f "${POLICY_FILE}" ]]; then
  echo "Policy file not found: ${POLICY_FILE}"
  exit 1
fi

if [[ -z "${POLICY_FILE}" && ! -d "${POLICY_DIR}" ]]; then
  echo "Policy directory not found: ${POLICY_DIR}"
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

  if jq -e --argjson expected "$expected_payload" \
    -f "${SCRIPT_DIR}/org-ruleset-matches.jq" <<<"$actual" >/dev/null; then
    return 0
  fi

  echo "Ruleset verification failed for ${ruleset_id}." >&2
  echo "Expected policy:" >&2
  jq '.' <<<"$expected_payload" >&2
  echo "Actual ruleset summary:" >&2
  jq '{
    name,
    target,
    enforcement,
    conditions,
    rules: [.rules[] | {type, parameters}]
  }' <<<"$actual" >&2
  return 1
}

sync_ruleset_file() {
  local policy_file="$1"
  local payload ruleset_name rulesets_file match_count ruleset_id aliases existing

  payload="$(jq -c '.' "$policy_file")"
  ruleset_name="$(jq -r '.name // empty' "$policy_file")"
  if [[ -z "$ruleset_name" ]]; then
    echo "Policy file must define .name: ${policy_file}"
    exit 1
  fi

  aliases='[]'
  if [[ -f "$RENAME_FILE" ]]; then
    aliases="$(jq -c --arg name "$ruleset_name" '.[$name] // []' "$RENAME_FILE")"
  fi
  rulesets_file="$(mktemp)"
  if ! list_rulesets >"$rulesets_file"; then
    rm -f "$rulesets_file"
    exit 1
  fi

  match_count="$(
    jq -s --arg name "$ruleset_name" --argjson aliases "$aliases" \
      '[.[] | select(.name as $n | $n == $name or ($aliases | index($n)) != null)] | length' \
      "$rulesets_file"
  )"

  if (( match_count > 1 )); then
    echo "Found ${match_count} canonical/legacy rulesets for '${ruleset_name}'. Refusing an ambiguous migration; existing protections are unchanged."
    rm -f "$rulesets_file"
    exit 1
  fi

  if (( match_count == 0 )); then
    echo "Creating org ruleset: ${ruleset_name}"
    ruleset_id="$(api POST "${API_BASE}/orgs/${ORG_NAME}/rulesets" "$payload" | jq -r '.id')"
  else
    ruleset_id="$(
      jq -r --arg name "$ruleset_name" --argjson aliases "$aliases" \
        'select(.name as $n | $n == $name or ($aliases | index($n)) != null) | .id' \
        "$rulesets_file"
    )"
    existing="$(api GET "${API_BASE}/orgs/${ORG_NAME}/rulesets/${ruleset_id}")"
    if ! jq -e --argjson expected "$payload" '.target == $expected.target' <<<"$existing" >/dev/null; then
      echo "Ruleset ${ruleset_id} has a different target; refusing to repurpose it." >&2
      exit 1
    fi
    echo "Updating org ruleset: ${ruleset_name} (${ruleset_id})"
    api PUT "${API_BASE}/orgs/${ORG_NAME}/rulesets/${ruleset_id}" "$payload" >/dev/null
  fi

  rm -f "$rulesets_file"

  verify_ruleset "$ruleset_id" "$payload"
  echo "Org ruleset sync complete: ${ruleset_name} (${ruleset_id})"
}

prepare_owner_policies() {
  local directory="${POLICY_DIR}/owner-only-merge"
  local mapping="${directory}/repositories.json"
  local repository team team_json team_id
  [[ -f "$mapping" ]] || return 0

  jq -e 'type == "object" and length > 0 and
    all(to_entries[];
      (.key | test("^[A-Za-z0-9_.-]+$")) and
      (.key != "." and .key != "..") and
      (.value | type == "string" and test("^[a-z0-9]+(-[a-z0-9]+)*$")))' \
    "$mapping" >/dev/null || { echo "Invalid owner-team mapping: $mapping" >&2; return 1; }

  while IFS=$'\t' read -r repository team; do
    if ! jq -e --arg team "$team" --arg repo "$repository" '
      .teams[$team].name == $team and
      (.teams[$team].repos[$repo] | IN("write", "push", "maintain", "admin"))
    ' "$ACCESS_POLICY_FILE" >/dev/null; then
      echo "Owner team $team needs a write/maintain/admin access-policy grant for $repository" >&2
      return 1
    fi
    if ! team_json="$(api GET "${API_BASE}/orgs/${ORG_NAME}/teams/${team}")"; then
      echo "Cannot resolve owner team $team for $repository; apply the access policy first." >&2
      return 1
    fi
    team_id="$(jq -er --arg slug "$team" '
      select(.slug == $slug) | .id | select(type == "number" and . > 0)
    ' <<<"$team_json")" || { echo "Invalid team response for $team" >&2; return 1; }
    jq --arg repo "$repository" --argjson team_id "$team_id" '
      .name = "Owner-only merges for \($repo)" |
      .conditions.repository_name.include = [$repo] |
      .bypass_actors += [{actor_id: $team_id, actor_type: "Team", bypass_mode: "pull_request"}]
    ' "${directory}/template.json" >"${WORK_DIR}/owner-${repository}.json"
    policy_files+=("${WORK_DIR}/owner-${repository}.json")
  done < <(jq -r 'to_entries | sort_by(.key)[] | [.key, .value] | @tsv' "$mapping")
}

if [[ "$CHECK_ACCESS" == true ]]; then
  policy_files=()
  prepare_owner_policies
  list_rulesets >/dev/null
  echo "App API access verified: organization rulesets listed and configured owner teams resolved; no writes performed."
elif [[ -n "${POLICY_FILE}" ]]; then
  sync_ruleset_file "$POLICY_FILE"
else
  policy_files=()
  while IFS= read -r policy_file; do
    policy_files+=("$policy_file")
  done < <(find "${POLICY_DIR}" -maxdepth 1 -type f -name '*.json' | sort)

  if [[ "${#policy_files[@]}" -eq 0 ]]; then
    echo "No ruleset policy files found in ${POLICY_DIR}"
    exit 1
  fi

  prepare_owner_policies

  for policy_file in "${policy_files[@]}"; do
    sync_ruleset_file "$policy_file"
  done
fi
