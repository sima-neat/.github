#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${ORG_NAME:-}" ]]; then
  echo "ORG_NAME is required"
  exit 1
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN is required"
  exit 1
fi

POLICY_DIR="${POLICY_DIR:-policies/org-rulesets}"
API_BASE="https://api.github.com"
API_VERSION="2022-11-28"

api() {
  local method="$1"
  local url="$2"
  local data="${3:-}"

  if [[ -n "$data" ]]; then
    curl -fsSL \
      -X "$method" \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer ${GITHUB_TOKEN}" \
      -H "X-GitHub-Api-Version: ${API_VERSION}" \
      "$url" \
      -d "$data"
  else
    curl -fsSL \
      -X "$method" \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer ${GITHUB_TOKEN}" \
      -H "X-GitHub-Api-Version: ${API_VERSION}" \
      "$url"
  fi
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
