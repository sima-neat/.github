"""Exercise command execution and failure classification without mutating packages."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/sdk-package.sh'
DPKG = 'dpkg: error: dpkg frontend lock was locked by another process with pid 998'
APT = 'E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 998 (apt-get)'


class PackageTests(unittest.TestCase):
    def run_helper(self, operation='configure', message=DPKG, failures=1, status=2, budget=3):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stub = root / 'dpkg'
            stub.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
p = pathlib.Path(os.environ['COUNT'])
n = int(p.read_text()) if p.exists() else 0
p.write_text(str(n + 1))
if n < int(os.environ['FAILURES']):
    print(os.environ['MESSAGE'], file=sys.stderr)
    sys.exit(int(os.environ['STATUS']))
print('configured')
''')
            stub.chmod(0o755)
            (root / 'apt-get').symlink_to(stub)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                       COUNT=str(root/'count'), FAILURES=str(failures), MESSAGE=message,
                       STATUS=str(status), SDK_PACKAGE_LOCK_TIMEOUT=str(budget),
                       SDK_PACKAGE_LOCK_INTERVAL='1')
            start = time.monotonic()
            result = subprocess.run(['bash', str(HELPER), '--inside', operation], env=env,
                                    text=True, capture_output=True, timeout=10)
            return result, int((root/'count').read_text()), time.monotonic()-start

    def test_immediate_success(self):
        result, calls, _ = self.run_helper(failures=0)
        self.assertEqual((result.returncode, calls), (0, 1))

    def test_transient_dpkg(self):
        result, calls, _ = self.run_helper()
        self.assertEqual((result.returncode, calls), (0, 2))
        self.assertIn('diagnostics', result.stderr)

    def test_apt_update_and_install(self):
        for operation in ('update', 'install'):
            result, calls, _ = self.run_helper(operation, APT, status=100)
            self.assertEqual((result.returncode, calls), (0, 2))

    def test_timeout(self):
        result, calls, elapsed = self.run_helper(failures=100, budget=1)
        self.assertEqual(result.returncode, 2)
        self.assertLessEqual(calls, 2)
        self.assertLess(elapsed, 4)
        self.assertIn('timeout', result.stderr)

    def test_unrelated_and_mixed_errors(self):
        for message, status in [('dpkg: error: broken package', 2),
                                (DPKG, 1),
                                (DPKG+'\ndpkg: error: broken package', 2),
                                ('E: Unable to locate package missing', 100),
                                (APT+'\nE: Failed to fetch repository', 100)]:
            operation = 'configure' if status != 100 else 'install'
            result, calls, _ = self.run_helper(operation, message, status=status)
            self.assertEqual((result.returncode, calls), (status, 1))
            self.assertIn(message, result.stderr)

    def test_host_streams_into_selected_container(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            docker = root/'docker'
            docker.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
if '-i' in sys.argv:
    pathlib.Path(os.environ['STREAM']).write_text(sys.stdin.read())
''')
            docker.chmod(0o755)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                       CALLS=str(root/'calls'), STREAM=str(root/'stream'))
            result = subprocess.run(['bash', str(HELPER), 'chosen-sdk', 'install',
                                     '-y', 'package with spaces'], env=env)
            self.assertEqual(result.returncode, 0)
            self.assertEqual((root/'stream').read_text(), HELPER.read_text())
            import json
            calls = [json.loads(line) for line in (root/'calls').read_text().splitlines()]
            self.assertEqual(calls[0], ['exec', 'chosen-sdk', 'test', '-r', '/etc/sdk-release'])
            self.assertEqual(calls[1][-8:], ['chosen-sdk', 'bash', '-s', '--', '--inside',
                                            'install', '-y', 'package with spaces'])

    def test_workflow_bundle_is_current(self):
        subprocess.run(['python3', str(ROOT/'scripts/bundle_sdk_package_helper.py'), '--check'], check=True)


if __name__ == '__main__':
    unittest.main()
