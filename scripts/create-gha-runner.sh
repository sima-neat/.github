#!/usr/bin/env bash
set -euo pipefail

API_TOKEN="${ORG_ADMIN_TOKEN:-}"
if [[ -z "${API_TOKEN}" ]]; then
  if [[ -t 0 ]]; then
    read -r -s -p "ORG_ADMIN_TOKEN (input hidden): " API_TOKEN
    echo
  fi
fi
if [[ -z "${API_TOKEN}" ]]; then
  echo "ORG_ADMIN_TOKEN is required."
  exit 1
fi
unset ORG_ADMIN_TOKEN
trap 'unset API_TOKEN registration_token remove_token' EXIT

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd"
    exit 1
  fi
}

run_as_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    run_as_root apt-get update
    run_as_root apt-get install -y --no-install-recommends "$@"
    return 0
  fi

  if command -v dnf >/dev/null 2>&1; then
    run_as_root dnf install -y "$@"
    return 0
  fi

  if command -v yum >/dev/null 2>&1; then
    run_as_root yum install -y "$@"
    return 0
  fi

  if command -v apk >/dev/null 2>&1; then
    run_as_root apk add --no-cache "$@"
    return 0
  fi

  return 1
}

ensure_jq() {
  if command -v jq >/dev/null 2>&1; then
    return 0
  fi

  echo "Installing jq..."
  if ! install_packages jq; then
    echo "Could not install jq automatically. Install jq and re-run this script."
    exit 1
  fi
}

ensure_unzip() {
  if command -v unzip >/dev/null 2>&1; then
    return 0
  fi

  echo "Installing unzip..."
  if ! install_packages unzip; then
    echo "Could not install unzip automatically. Install unzip and re-run this script."
    exit 1
  fi
}

ensure_aws_cli() {
  local platform="$1"
  local arch="$2"
  local aws_arch
  local aws_zip
  local tmp_dir

  if command -v aws >/dev/null 2>&1; then
    return 0
  fi

  if [[ "$platform" != "linux" ]]; then
    echo "AWS CLI is missing. Automatic AWS CLI install is only implemented for Linux."
    echo "Install AWS CLI v2 for this platform and re-run this script."
    exit 1
  fi

  case "$arch" in
    x64) aws_arch="x86_64" ;;
    arm64) aws_arch="aarch64" ;;
    *)
      echo "Unsupported AWS CLI architecture: $arch"
      exit 1
      ;;
  esac

  ensure_unzip
  tmp_dir="$(mktemp -d)"
  aws_zip="$tmp_dir/awscliv2.zip"

  echo "Installing AWS CLI v2 for linux-${aws_arch}..."
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip" -o "$aws_zip"
  unzip -q "$aws_zip" -d "$tmp_dir"
  run_as_root "$tmp_dir/aws/install" --bin-dir /usr/local/bin --install-dir /usr/local/aws-cli --update
  rm -rf "$tmp_dir"
}

prompt_default() {
  local prompt="$1"
  local default="$2"
  local value
  read -r -p "${prompt} [${default}]: " value
  if [[ -z "$value" ]]; then
    echo "$default"
  else
    echo "$value"
  fi
}

prompt_yes_no() {
  local prompt="$1"
  local default="$2"
  local value
  while true; do
    read -r -p "${prompt} (${default}/$( [[ "$default" == "y" ]] && echo "n" || echo "y" )): " value
    value="${value:-$default}"
    case "$value" in
      y|Y) echo "y"; return 0 ;;
      n|N) echo "n"; return 0 ;;
      *) echo "Please answer y or n." ;;
    esac
  done
}

api_post() {
  local url="$1"
  local body_file
  local http_code

  body_file="$(mktemp)"
  http_code="$(curl -sS \
    -o "$body_file" \
    -w "%{http_code}" \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${API_TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$url" || true)"

  if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
    echo "GitHub API request failed: POST $url" >&2
    echo "HTTP status: $http_code" >&2
    if [[ -s "$body_file" ]]; then
      jq -r '.message // empty' "$body_file" 2>/dev/null | sed 's/^/GitHub message: /' >&2 || true
      cat "$body_file" >&2
    fi
    rm -f "$body_file"
    echo >&2
    echo "Check that ORG_ADMIN_TOKEN is valid and has permission to manage Actions runners for this organization or repository." >&2
    echo "Classic PATs usually need admin:org for org runners, or repo/admin access for repo runners." >&2
    exit 1
  fi

  cat "$body_file"
  rm -f "$body_file"
}

download_file() {
  local url="$1"
  local output="$2"

  curl --fail --location --retry 3 --retry-delay 2 \
    --connect-timeout 20 \
    --output "$output" \
    "$url"
}

sha256_file() {
  local path="$1"

  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
    return 0
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
    return 0
  fi

  echo "Missing required command: shasum or sha256sum"
  exit 1
}

verify_runner_archive() {
  local archive_name="$1"
  local expected_sha256="$2"
  local actual_sha256

  if [[ -n "$expected_sha256" && "$expected_sha256" != "null" ]]; then
    actual_sha256="$(sha256_file "$archive_name")"
    if [[ "$actual_sha256" != "$expected_sha256" ]]; then
      echo "Checksum mismatch for $archive_name"
      echo "Expected: $expected_sha256"
      echo "Actual:   $actual_sha256"
      exit 1
    fi
  else
    echo "No SHA256 digest published for $archive_name; validating archive format only."
  fi

  tar tzf "$archive_name" >/dev/null
}

fetch_runner_token() {
  local endpoint="$1"
  local resp
  local token
  resp="$(api_post "$endpoint")"
  token="$(jq -r '.token' <<<"$resp")"
  if [[ -z "$token" || "$token" == "null" ]]; then
    echo "Failed to obtain token from: $endpoint"
    echo "$resp"
    exit 1
  fi
  echo "$token"
}

detect_platform() {
  case "$(uname -s)" in
    Linux) echo "linux" ;;
    Darwin) echo "osx" ;;
    *) echo "linux" ;;
  esac
}

detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64) echo "x64" ;;
    arm64|aarch64) echo "arm64" ;;
    *) echo "x64" ;;
  esac
}

random_suffix() {
  local chars
  chars="$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 6 || true)"
  if [[ -z "$chars" ]]; then
    chars="$(date +%s | tail -c 7)"
  fi
  echo "$chars"
}

detect_runner_group() {
  local platform="$1"
  local arch="$2"

  if [[ -f "/etc/sdk-release" ]] && grep -q "^SDK Version" /etc/sdk-release; then
    echo "elxr-sdk-containers"
    return 0
  fi

  if [[ -f "/etc/buildinfo" ]] && grep -Eq "^MACHINE[[:space:]]*=[[:space:]]*modalix$" /etc/buildinfo; then
    echo "modalix-devkit"
    return 0
  fi

  if [[ "$platform" == "linux" && "$arch" == "x64" ]]; then
    echo "generic-x86-linux"
    return 0
  fi

  echo "Default"
}

has_systemd() {
  local state

  if [[ "$platform" != "linux" ]] ||
    [[ ! -d /run/systemd/system ]] ||
    ! command -v systemctl >/dev/null 2>&1; then
    return 1
  fi

  state="$(systemctl is-system-running 2>/dev/null || true)"
  [[ "$state" == "running" || "$state" == "degraded" ]]
}

has_supervisor() {
  command -v supervisorctl >/dev/null 2>&1 &&
    supervisorctl status >/dev/null 2>&1
}

safe_service_name() {
  local name="$1"
  printf '%s' "$name" | tr -cs '[:alnum:]_.-' '-' | sed -E 's/^-+//; s/-+$//'
}

resolve_start_mode() {
  local requested="$1"

  if [[ "$requested" != "auto" ]]; then
    echo "$requested"
    return 0
  fi

  if [[ "$platform" == "osx" ]] && command -v launchctl >/dev/null 2>&1; then
    echo "launchd"
    return 0
  fi

  if has_systemd; then
    echo "systemd"
    return 0
  fi

  if has_supervisor; then
    echo "supervisor"
    return 0
  fi

  echo "nohup"
}

stop_existing_runner_processes() {
  local service_name="$1"
  local launchd_label="com.github.actions.runner.${service_name}"
  local launchd_plist="${HOME}/Library/LaunchAgents/${launchd_label}.plist"
  local old_pid

  if [[ "$platform" == "osx" ]] && command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/${UID}/${launchd_label}" >/dev/null 2>&1 || true
    launchctl bootout "gui/${UID}" "$launchd_plist" >/dev/null 2>&1 || true
  fi

  if [[ -x "./svc.sh" ]] && has_systemd; then
    sudo ./svc.sh stop || true
    sudo ./svc.sh uninstall || true
  fi

  if has_supervisor; then
    supervisorctl stop "$service_name" >/dev/null 2>&1 || true
    supervisorctl remove "$service_name" >/dev/null 2>&1 || true
  fi

  if [[ -f "runner.pid" ]]; then
    old_pid="$(cat runner.pid 2>/dev/null || true)"
    if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" >/dev/null 2>&1; then
      kill "$old_pid" || true
    fi
    rm -f runner.pid
  fi
}

start_with_supervisor() {
  local service_name="$1"
  local conf_dir="/etc/supervisor/conf.d"
  local conf_path="${conf_dir}/${service_name}.conf"
  local local_conf="./${service_name}.supervisor.conf"

  if ! has_supervisor; then
    echo "supervisord is not running or supervisorctl is not available."
    exit 1
  fi

  cat >"$local_conf" <<EOF
[program:${service_name}]
command=${PWD}/run.sh
directory=${PWD}
autostart=true
autorestart=true
startsecs=3
startretries=3
stopsignal=TERM
stopasgroup=true
killasgroup=true
stdout_logfile=/var/log/supervisor/${service_name}.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
stderr_logfile=/var/log/supervisor/${service_name}.err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=3
EOF

  run_as_root mkdir -p "$conf_dir" /var/log/supervisor
  run_as_root cp "$local_conf" "$conf_path"
  supervisorctl reread
  supervisorctl update
  supervisorctl restart "$service_name"
  echo "Runner started with supervisord as ${service_name}."
}

start_with_nohup() {
  echo "Starting runner with nohup..."
  nohup ./run.sh >runner.log 2>&1 &
  echo "$!" >runner.pid
  echo "Runner started with PID $(cat runner.pid). Logs: ${PWD}/runner.log"
}

start_with_launchd() {
  local service_name="$1"
  local label="com.github.actions.runner.${service_name}"
  local plist_dir="${HOME}/Library/LaunchAgents"
  local plist_path="${plist_dir}/${label}.plist"

  if [[ "$platform" != "osx" ]] || ! command -v launchctl >/dev/null 2>&1; then
    echo "launchd was requested, but this host does not support launchd."
    exit 1
  fi

  mkdir -p "$plist_dir"
  cat >"$plist_path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PWD}/run.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PWD}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${PWD}/runner.log</string>
  <key>StandardErrorPath</key>
  <string>${PWD}/runner.err.log</string>
</dict>
</plist>
EOF

  launchctl bootout "gui/${UID}" "$plist_path" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/${UID}" "$plist_path"
  launchctl kickstart -k "gui/${UID}/${label}"
  echo "Runner started with launchd as ${label}."
  echo "Logs: ${PWD}/runner.log and ${PWD}/runner.err.log"
}

echo "GitHub Actions Runner Setup"
echo

for cmd in curl tar uname hostname; do
  require_cmd "$cmd"
done

scope="$(prompt_default "Runner scope: org or repo" "org")"
scope="$(echo "$scope" | tr '[:upper:]' '[:lower:]')"
if [[ "$scope" != "org" && "$scope" != "repo" ]]; then
  echo "Invalid scope. Use 'org' or 'repo'."
  exit 1
fi

org_name="$(prompt_default "GitHub organization" "sima-neat")"
repo_name=""
if [[ "$scope" == "repo" ]]; then
  repo_name="$(prompt_default "Repository name (without org prefix)" ".github")"
fi

if [[ "$scope" == "org" ]]; then
  url="https://github.com/${org_name}"
  registration_endpoint="https://api.github.com/orgs/${org_name}/actions/runners/registration-token"
  remove_endpoint="https://api.github.com/orgs/${org_name}/actions/runners/remove-token"
else
  url="https://github.com/${org_name}/${repo_name}"
  registration_endpoint="https://api.github.com/repos/${org_name}/${repo_name}/actions/runners/registration-token"
  remove_endpoint="https://api.github.com/repos/${org_name}/${repo_name}/actions/runners/remove-token"
fi

platform="${RUNNER_PLATFORM:-$(detect_platform)}"
arch="${RUNNER_ARCH:-$(detect_arch)}"
if [[ "$platform" != "linux" && "$platform" != "osx" ]]; then
  echo "Unsupported detected platform: $platform"
  echo "Set RUNNER_PLATFORM=linux|osx to override."
  exit 1
fi
if [[ "$arch" != "x64" && "$arch" != "arm64" ]]; then
  echo "Unsupported detected architecture: $arch"
  echo "Set RUNNER_ARCH=x64|arm64 to override."
  exit 1
fi
echo "Detected runner target: ${platform}-${arch}"

ensure_jq
ensure_aws_cli "$platform" "$arch"

runner_name_default="$(hostname)-${platform}-${arch}-$(random_suffix)"
runner_name="$(prompt_default "Runner name" "$runner_name_default")"
runner_service_name="github-actions-runner-$(safe_service_name "$runner_name")"
labels_default="self-hosted,${platform},${arch}"
labels="$(prompt_default "Runner labels (comma-separated)" "$labels_default")"
work_folder="$(prompt_default "Runner work folder" "_work")"
ephemeral="$(prompt_yes_no "Ephemeral runner" "n")"
replace_existing="$(prompt_yes_no "Replace existing runner with same name" "y")"

install_root_default="$PWD/actions-runner-${org_name}-${runner_name}"
install_root="$(prompt_default "Install directory" "$install_root_default")"

start_mode="$(prompt_default "Start mode: auto, launchd, systemd, supervisor, nohup, foreground, none" "auto")"
start_mode="$(echo "$start_mode" | tr '[:upper:]' '[:lower:]')"
case "$start_mode" in
  auto|launchd|systemd|supervisor|nohup|foreground|none) ;;
  *)
    echo "Invalid start mode: $start_mode"
    echo "Use auto, launchd, systemd, supervisor, nohup, foreground, or none."
    exit 1
    ;;
esac

if [[ -d "$install_root" ]] && [[ -n "$(ls -A "$install_root" 2>/dev/null || true)" ]]; then
  if [[ -f "$install_root/.runner" && -x "$install_root/config.sh" ]]; then
    existing_action="$(prompt_default "Existing runner setup found. Action: skip or recreate" "skip")"
    existing_action="$(echo "$existing_action" | tr '[:upper:]' '[:lower:]')"
    if [[ "$existing_action" == "skip" ]]; then
      echo "Skipping setup. Existing runner left unchanged at: $install_root"
      exit 0
    fi
    if [[ "$existing_action" != "recreate" ]]; then
      echo "Invalid action: $existing_action (use skip or recreate)"
      exit 1
    fi
  else
    continue_existing="$(prompt_yes_no "Install directory exists and is not empty. Continue anyway" "n")"
    if [[ "$continue_existing" != "y" ]]; then
      echo "Aborted."
      exit 1
    fi
  fi
fi

mkdir -p "$install_root"
cd "$install_root"

if [[ -f ".runner" && -x "./config.sh" ]]; then
  echo "De-registering existing runner..."
  stop_existing_runner_processes "$runner_service_name"
  remove_token="$(fetch_runner_token "$remove_endpoint")"
  ./config.sh remove --token "$remove_token"
fi

if [[ ! -x "./config.sh" ]]; then
  echo "Resolving latest runner package for ${platform}-${arch}..."
  release_json="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest)"
  asset_json="$(jq -c --arg p "$platform" --arg a "$arch" '.assets[] | select(.name | test("^actions-runner-" + $p + "-" + $a + "-[0-9.]+\\.tar\\.gz$"))' <<<"$release_json" | head -n1)"
  asset_url="$(jq -r '.browser_download_url' <<<"$asset_json")"
  asset_digest="$(jq -r '.digest // empty' <<<"$asset_json" | sed 's/^sha256://')"

  if [[ -z "$asset_url" || "$asset_url" == "null" ]]; then
    echo "Could not find runner download for ${platform}-${arch}."
    exit 1
  fi

  archive_name="$(basename "$asset_url")"
  if [[ ! -f "$archive_name" ]]; then
    echo "Downloading ${archive_name}..."
    download_file "$asset_url" "$archive_name"
  fi

  echo "Verifying ${archive_name}..."
  verify_runner_archive "$archive_name" "$asset_digest"

  echo "Extracting ${archive_name}..."
  tar xzf "$archive_name"
fi

echo "Requesting GitHub Actions runner registration token..."
registration_token="$(fetch_runner_token "$registration_endpoint")"

config_args=(
  --unattended
  --url "$url"
  --token "$registration_token"
  --name "$runner_name"
  --work "$work_folder"
  --labels "$labels"
)

if [[ "$scope" == "org" ]]; then
  runner_group_default="${RUNNER_GROUP:-$(detect_runner_group "$platform" "$arch")}"
  echo "Detected runner group: ${runner_group_default}"
  runner_group="$(prompt_default "Runner group name" "$runner_group_default")"
  config_args+=(--runnergroup "$runner_group")
fi

if [[ "$ephemeral" == "y" ]]; then
  config_args+=(--ephemeral)
fi

if [[ "$replace_existing" == "y" ]]; then
  config_args+=(--replace)
fi

echo "Configuring runner for ${url}..."
./config.sh "${config_args[@]}"

resolved_start_mode="$(resolve_start_mode "$start_mode")"
echo "Resolved start mode: ${resolved_start_mode}"

case "$resolved_start_mode" in
  systemd)
    if ! has_systemd; then
      echo "systemd was requested, but systemd is not running on this host."
      exit 1
    fi
    echo "Installing systemd service..."
    sudo ./svc.sh install
    sudo ./svc.sh start
    echo "Runner systemd service started."
    ;;
  supervisor)
    start_with_supervisor "$runner_service_name"
    ;;
  launchd)
    start_with_launchd "$runner_service_name"
    ;;
  nohup)
    start_with_nohup
    ;;
  foreground)
    echo "Starting runner in foreground..."
    ./run.sh
    ;;
  none)
    echo "Setup complete. Start later with: cd \"$install_root\" && ./run.sh"
    ;;
  *)
    echo "Unsupported resolved start mode: ${resolved_start_mode}"
    exit 1
    ;;
esac
