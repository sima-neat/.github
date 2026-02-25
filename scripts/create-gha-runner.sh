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

for cmd in curl jq tar uname hostname; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd"
    exit 1
  fi
done

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
  curl -fsSL \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${API_TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$url"
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

echo "GitHub Actions Runner Setup"
echo

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

runner_name_default="$(hostname)-${platform}-${arch}-$(random_suffix)"
runner_name="$(prompt_default "Runner name" "$runner_name_default")"
labels_default="self-hosted,${platform},${arch}"
labels="$(prompt_default "Runner labels (comma-separated)" "$labels_default")"
work_folder="$(prompt_default "Runner work folder" "_work")"
ephemeral="$(prompt_yes_no "Ephemeral runner" "n")"
replace_existing="$(prompt_yes_no "Replace existing runner with same name" "y")"

install_root_default="$PWD/actions-runner-${org_name}-${runner_name}"
install_root="$(prompt_default "Install directory" "$install_root_default")"

as_service="$(prompt_yes_no "Install and start as service (requires sudo)" "y")"
run_after_setup="$(prompt_yes_no "Run runner immediately after setup" "y")"

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
  if [[ -x "./svc.sh" ]]; then
    sudo ./svc.sh stop || true
    sudo ./svc.sh uninstall || true
  fi
  remove_token="$(fetch_runner_token "$remove_endpoint")"
  ./config.sh remove --token "$remove_token"
fi

if [[ ! -x "./config.sh" ]]; then
  echo "Resolving latest runner package for ${platform}-${arch}..."
  release_json="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest)"
  asset_url="$(jq -r --arg p "$platform" --arg a "$arch" '.assets[] | select(.name | test("^actions-runner-" + $p + "-" + $a + "-[0-9.]+\\.tar\\.gz$")) | .browser_download_url' <<<"$release_json" | head -n1)"

  if [[ -z "$asset_url" || "$asset_url" == "null" ]]; then
    echo "Could not find runner download for ${platform}-${arch}."
    exit 1
  fi

  archive_name="$(basename "$asset_url")"
  if [[ ! -f "$archive_name" ]]; then
    echo "Downloading ${archive_name}..."
    curl -fL -o "$archive_name" "$asset_url"
  fi

  echo "Extracting ${archive_name}..."
  tar xzf "$archive_name"
fi

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

if [[ "$as_service" == "y" ]]; then
  echo "Installing service..."
  sudo ./svc.sh install
  sudo ./svc.sh start
  echo "Runner service started."
elif [[ "$run_after_setup" == "y" ]]; then
  echo "Starting runner in foreground..."
  ./run.sh
else
  echo "Setup complete. Start later with: cd \"$install_root\" && ./run.sh"
fi
