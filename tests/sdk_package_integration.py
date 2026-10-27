"""Real package locks in a private disposable container; no network package install."""
import argparse
import os
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    container = 'sdk-package-test-' + uuid.uuid4().hex[:12]
    subprocess.run(['docker', 'run', '-d', '--name', container, '--entrypoint', 'bash',
                    args.image, '-c', 'touch /etc/sdk-release; sleep 600'], check=True,
                   stdout=subprocess.DEVNULL)
    try:
        # Real fcntl locks, not flock: dpkg/APT use POSIX record locks.
        for operation, path, extra, expected in [
                ('configure', '/var/lib/dpkg/lock-frontend', [], 2),
                ('update', '/var/lib/apt/lists/lock',
                 ['-o', 'Dir::Etc::sourcelist=/dev/null', '-o', 'Dir::Etc::sourceparts=-'], 100),
                ('install', '/var/lib/dpkg/lock-frontend', ['-y', '--no-download'], 100)]:
            for persistent in (False, True):
                holder = subprocess.Popen(['docker', 'exec', '--user', 'root', container,
                    'python3', '-c',
                    'import fcntl,time,os; '
                    f'os.makedirs({str(Path(path).parent)!r},exist_ok=True); '
                    f'f=open({path!r},"a"); fcntl.lockf(f,fcntl.LOCK_EX); '
                    'print("locked",flush=True); time.sleep(3)'],
                    stdout=subprocess.PIPE, text=True)
                assert holder.stdout.readline().strip() == 'locked'
                start = time.monotonic()
                result = subprocess.run(['bash', str(ROOT/'scripts/sdk-package.sh'), container,
                                         operation, *extra], text=True, capture_output=True,
                    env=dict(os.environ, SDK_PACKAGE_LOCK_INTERVAL='1',
                             SDK_PACKAGE_LOCK_TIMEOUT='1' if persistent else '10'), timeout=30)
                elapsed = time.monotonic()-start
                # Owner must still be alive after the helper's bounded refusal.
                if persistent:
                    assert holder.poll() is None, result.stderr
                    assert result.returncode == expected, result.stderr
                    assert 'lock timeout' in result.stderr and elapsed < 5, result.stderr
                else:
                    assert result.returncode == 0, result.stderr
                    assert 'lock contention' in result.stderr, result.stderr
                holder.wait(timeout=10)
                # Lock inode must remain; no lock-file removal by the helper.
                subprocess.run(['docker', 'exec', container, 'test', '-f', path], check=True)
                print(f'{operation}: persistent={persistent} status={result.returncode} elapsed={elapsed:.1f}s')
    finally:
        subprocess.run(['docker', 'rm', '-f', container], check=True, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
