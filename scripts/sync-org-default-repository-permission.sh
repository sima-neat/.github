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

POLICY_FILE="${POLICY_FILE:-policies/org-default-repository-permission/config.json}"
API_BASE="https://api.github.com"
API_VERSION="2022-11-28"

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

permission="$(jq -r '.default_repository_permission // empty' "$POLICY_FILE" | tr '[:upper:]' '[:lower:]')"

case "$permission" in
  none|read|write|admin) ;;
  "")
    echo "Policy file must define .default_repository_permission"
    exit 1
    ;;
  *)
    echo "Invalid default_repository_permission '$permission'. Allowed: none, read, write, admin"
    exit 1
    ;;
esac

current_permission="$(api GET "${API_BASE}/orgs/${ORG_NAME}" | jq -r '.default_repository_permission')"

if [[ "$current_permission" == "$permission" ]]; then
  echo "No change: org default repository permission already '${current_permission}'."
  exit 0
fi

echo "Setting org default repository permission: ${current_permission} -> ${permission}"
payload="$(jq -cn --arg permission "$permission" '{default_repository_permission: $permission}')"
api PATCH "${API_BASE}/orgs/${ORG_NAME}" "$payload" >/dev/null
echo "Org default repository permission sync complete."
