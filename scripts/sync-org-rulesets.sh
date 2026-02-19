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

POLICY_DIR="${POLICY_DIR:-policies/org-rulesets}"
API_BASE="https://api.github.com"
API_VERSION="2022-11-28"

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

echo "Fetching existing org rulesets for ${ORG_NAME}..."
existing="$(api GET "${API_BASE}/orgs/${ORG_NAME}/rulesets")"

shopt -s nullglob
for file in "${POLICY_DIR}"/*.json; do
  name="$(jq -r '.name' "$file")"
  payload="$(jq -c . "$file")"
  id="$(jq -r --arg name "$name" '.[] | select(.name == $name) | .id' <<<"$existing" | head -n1)"

  if [[ -n "${id}" ]]; then
    echo "Updating ruleset: ${name} (id=${id})"
    api PUT "${API_BASE}/orgs/${ORG_NAME}/rulesets/${id}" "$payload" >/dev/null
  else
    echo "Creating ruleset: ${name}"
    api POST "${API_BASE}/orgs/${ORG_NAME}/rulesets" "$payload" >/dev/null
  fi
done

echo "Org ruleset sync complete."
