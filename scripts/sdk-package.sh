#!/usr/bin/env bash
# Host entry point: sdk-package.sh CONTAINER {configure|update|install|clean} [ARGS...]
# The same revision is streamed into the selected container; no host package mutation.
set -euo pipefail
if [[ "${1:-}" != --inside ]]; then
  [[ $# -ge 2 && -n "$1" && "$1" != -* ]] || {
    echo 'Usage: sdk-package.sh CONTAINER {configure|update|install|clean} [ARGS...]' >&2
    exit 64
  }
  container=$1
  shift
  docker exec "$container" test -r /etc/sdk-release
  exec docker exec -i --user root -e LC_ALL=C -e DEBIAN_FRONTEND=noninteractive \
    -e "SDK_PACKAGE_LOCK_TIMEOUT=${SDK_PACKAGE_LOCK_TIMEOUT:-120}" \
    -e "SDK_PACKAGE_LOCK_INTERVAL=${SDK_PACKAGE_LOCK_INTERVAL:-5}" \
    "$container" bash -s -- --inside "$@" < "${BASH_SOURCE[0]}"
fi
shift
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
budget=${SDK_PACKAGE_LOCK_TIMEOUT:-120}
interval=${SDK_PACKAGE_LOCK_INTERVAL:-5}
[[ "$budget" =~ ^[0-9]+$ && "$interval" =~ ^[1-9][0-9]*$ ]] || {
  echo 'Lock timeout must be nonnegative seconds; interval must be positive seconds.' >&2
  exit 64
}
operation=${1:-}
[[ $# -gt 0 ]] && shift
case "$operation" in
  configure) command=(dpkg --configure -a "$@") ;;
  update|install|clean)
    # Use one retry deadline, rather than multiplying it by APT's native wait.
    command=(apt-get -o DPkg::Lock::Timeout=0 "$operation" "$@") ;;
  *) echo "Unsupported SDK package operation: $operation" >&2; exit 64 ;;
esac
log=$(mktemp)
trap 'rm -f "$log"' EXIT
# Match only known lock failures. Other error lines make the operation fatal.
dpkg_lock='^dpkg: error: (dpkg frontend lock|dpkg database lock|dpkg database area) (was locked|is locked) by (another process|.+ process with pid [0-9]+)'
apt_lock='^E: (Could not get lock /|Unable to acquire (the dpkg frontend lock|the dpkg administration directory|the dpkg database lock|the dpkg lock|the administration directory)|Unable to lock (directory /|the administration directory))'
lock_error="$dpkg_lock|$apt_lock"
report_owner() {
  local owner
  owner=$(sed -nE 's/.*(with pid |process )([0-9]+).*/\2/p' "$log" | head -n 1)
  echo "SDK package lock diagnostics (operation=$operation, elapsed=${SECONDS}s):" >&2
  if [[ -n "$owner" ]]; then
    ps -p "$owner" -o pid,ppid,etime,args >&2 || true
  fi
  # No signal is sent, and no lock file is removed. Snapshot may race with exit.
  # shellcheck disable=SC2009
  ps -eo pid,ppid,etime,args 2>/dev/null | grep -E '[a]pt|[d]pkg|[u]nattended' >&2 || true
}
SECONDS=0
while :; do
  if "${command[@]}" >"$log" 2>&1; then
    cat "$log"
    exit 0
  else
    status=$?
  fi
  cat "$log" >&2
  if ! { [[ "$operation" == configure && $status == 2 ]] ||
         [[ "$operation" != configure && $status == 100 ]]; } ||
     ! grep -Eq "$lock_error" "$log" ||
     { grep -E '^(dpkg: error:|E:)' "$log" | grep -Evq "$lock_error"; }; then
    exit "$status"
  fi
  report_owner
  remaining=$((budget - SECONDS))
  if ((remaining <= 0)); then
    echo "SDK package lock timeout after ${SECONDS}s (budget=${budget}s): $operation" >&2
    exit "$status"
  fi
  delay=$interval
  ((delay <= remaining)) || delay=$remaining
  echo "SDK package lock contention; retrying $operation in ${delay}s." >&2
  sleep "$delay"
done
