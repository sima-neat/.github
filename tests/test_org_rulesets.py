"""Exercise the real sync shell with a local, stateful GitHub API double.

These tests verify payloads and sync behavior, not GitHub's merge authorization.
See the scratch acceptance matrix in policies/org-rulesets/README.md.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "policies/org-rulesets"

# Replaces curl only; jq, bash, generation and verification are the real code.
FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs
args = sys.argv[1:]
def arg(name, default=None):
    return args[args.index(name)+1] if name in args else default
path = Path(os.environ['MOCK_STATE'])
state = json.loads(path.read_text())
url = next(x for x in args if x.startswith('https://'))
route = urlparse(url)
method = arg('-X')
state['calls'].append([method, route.path])
status = 200
if '/teams/' in route.path:
    slug = route.path.rsplit('/', 1)[1]
    body = state['teams'].get(slug)
    if body is None:
        status, body = 404, {'message': 'Not Found'}
elif method == 'GET' and route.path.endswith('/rulesets'):
    # Force two pages to prove that a legacy rule on page 2 is found.
    page = int(parse_qs(route.query)['page'][0])
    body = list(state['rulesets'].values())[page-1:page]
elif method == 'GET':
    body = state['rulesets'][route.path.rsplit('/', 1)[1]]
    body = json.loads(json.dumps(body))
    if state.get('drift') == body['name']:
        body['rules'][0]['parameters']['update_allows_fetch_and_merge'] = True
elif method in ('PUT', 'POST'):
    body = json.loads(arg('-d'))
    ident = route.path.rsplit('/', 1)[1] if method == 'PUT' else str(100+len(state['rulesets']))
    body['id'] = int(ident)
    state['rulesets'][ident] = body
else:
    raise AssertionError(method)
path.write_text(json.dumps(state))
Path(arg('-D')).write_text('HTTP/1.1 %s\r\n' % status)
Path(arg('-o')).write_text(json.dumps(body))
print(status, end='')
'''


class RulesetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.policies = self.root / 'policies'
        shutil.copytree(POLICIES, self.policies)
        self.access = self.root / 'access.json'
        shutil.copyfile(ROOT / 'policies/access/config.json', self.access)
        self.state = self.root / 'state.json'
        self.write_state({'rulesets': {}, 'teams': {
            'owners-linux': {'id': 11, 'slug': 'owners-linux'},
            'owners-other': {'id': 22, 'slug': 'owners-other'},
        }, 'calls': []})
        binary = self.root / 'bin'
        binary.mkdir()
        curl = binary / 'curl'
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{binary}:{os.environ["PATH"]}',
                        ORG_NAME='test-org', GH_TOKEN='fake-test-token',
                        POLICY_DIR=str(self.policies), ACCESS_POLICY_FILE=str(self.access),
                        MOCK_STATE=str(self.state))
        self.env.pop('POLICY_FILE', None)

    def write_state(self, state):
        self.state.write_text(json.dumps(state))

    def read_state(self):
        return json.loads(self.state.read_text())

    def run_sync(self, success=True, args=()):
        result = subprocess.run(['bash', str(ROOT / 'scripts/sync-org-rulesets.sh'), *args],
                                env=self.env, text=True, capture_output=True, cwd=ROOT)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def matches(self, expected, actual):
        result = subprocess.run(['jq', '-e', '--argjson', 'expected', json.dumps(expected),
                                 '-f', str(ROOT / 'scripts/org-ruleset-matches.jq')],
                                input=json.dumps(actual), text=True, capture_output=True)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode == 0

    def test_update_only_verification_detects_parameter_drift(self):
        expected = json.loads((POLICIES / 'owner-only-merge/template.json').read_text())
        self.assertTrue(self.matches(expected, expected))
        actual = copy.deepcopy(expected)
        actual['rules'][0]['parameters']['update_allows_fetch_and_merge'] = True
        self.assertFalse(self.matches(expected, actual))

    def test_every_managed_review_parameter_is_checked(self):
        expected = json.loads((POLICIES / 'require-two-approvals.json').read_text())
        for key, value in expected['rules'][0]['parameters'].items():
            with self.subTest(key=key):
                actual = copy.deepcopy(expected)
                actual['rules'][0]['parameters'][key] = (
                    not value if isinstance(value, bool) else [] if isinstance(value, list) else 1)
                self.assertFalse(self.matches(expected, actual))

    def test_verification_checks_all_rules_and_bypasses(self):
        expected = json.loads((POLICIES / 'protect-main-develop.json').read_text())
        actual = copy.deepcopy(expected)
        actual['id'] = 123  # Server metadata does not cause drift.
        actual['rules'].reverse()
        actual['rules'][-1]['parameters']['required_reviewers'] = []  # API default.
        self.assertTrue(self.matches(expected, actual))
        for field in ('rules', 'bypass_actors'):
            actual = copy.deepcopy(expected)
            actual[field] = []
            self.assertFalse(self.matches(expected, actual))
        actual = copy.deepcopy(expected)
        del actual['rules'][0]['parameters']['require_code_owner_review']
        self.assertFalse(self.matches(expected, actual))

    def test_rename_updates_old_id_on_second_page_and_is_repeatable(self):
        state = self.read_state()
        old = json.loads((self.policies / 'require-two-approvals.json').read_text())
        old['name'] = 'Require two approvals for linux'
        state['rulesets'] = {'5': {'id': 5, 'name': 'Unmanaged policy', 'target': 'branch'},
                             '8': dict(old, id=8)}
        self.write_state(state)
        self.run_sync()
        state = self.read_state()
        self.assertEqual(state['rulesets']['8']['name'], 'Require two approvals')
        self.assertIn(['PUT', '/orgs/test-org/rulesets/8'], state['calls'])
        first_rulesets = state['rulesets']
        self.run_sync()
        self.assertEqual(self.read_state()['rulesets'], first_rulesets)
        self.assertFalse(any(call[0] == 'DELETE' for call in self.read_state()['calls']))

    def test_ambiguous_migration_fails_without_modifying_either_review_rule(self):
        state = self.read_state()
        state['rulesets'] = {
            '1': {'id': 1, 'name': 'Require two approvals for linux'},
            '2': {'id': 2, 'name': 'Require two approvals'},
        }
        self.write_state(state)
        self.env['POLICY_FILE'] = str(self.policies / 'require-two-approvals.json')
        result = self.run_sync(success=False)
        self.assertIn('ambiguous migration', result.stdout)
        self.assertTrue(all(method == 'GET' for method, _ in self.read_state()['calls']))

    def test_two_repository_mappings_have_no_cross_team_bypass(self):
        (self.policies / 'owner-only-merge/repositories.json').write_text(
            json.dumps({'linux': 'owners-linux', 'other': 'owners-other'}))
        access = json.loads(self.access.read_text())
        access['teams']['owners-other'] = {'name': 'owners-other', 'repos': {'other': 'maintain', 'linux': 'write'}}
        access['teams']['owners-linux']['repos']['other'] = 'write'
        self.access.write_text(json.dumps(access))
        self.run_sync()
        rulesets = list(self.read_state()['rulesets'].values())
        for repo, team_id in (('linux', 11), ('other', 22)):
            rule = next(r for r in rulesets if r['name'] == f'Owner-only merges for {repo}')
            self.assertEqual(rule['conditions']['repository_name']['include'], [repo])
            self.assertEqual(rule['conditions']['ref_name']['include'], ['refs/heads/main'])
            self.assertEqual(rule['rules'], [{'type': 'update', 'parameters': {'update_allows_fetch_and_merge': False}}])
            self.assertEqual(rule['bypass_actors'], [
                {'actor_id': None, 'actor_type': 'OrganizationAdmin', 'bypass_mode': 'always'},
                {'actor_id': team_id, 'actor_type': 'Team', 'bypass_mode': 'pull_request'}])

    def test_missing_team_fails_before_any_ruleset_write(self):
        state = self.read_state()
        del state['teams']['owners-linux']
        self.write_state(state)
        result = self.run_sync(success=False)
        self.assertIn('Cannot resolve owner team owners-linux', result.stderr)
        self.assertTrue(all(method == 'GET' for method, _ in self.read_state()['calls']))

    def test_legacy_token_is_not_an_authentication_fallback(self):
        self.env.pop('GH_TOKEN')
        self.env['ORG_ADMIN_TOKEN'] = 'unused-legacy-test-token'
        self.env['GITHUB_TOKEN'] = 'unused-workflow-test-token'
        result = self.run_sync(success=False)
        self.assertIn('Set GH_TOKEN to a GitHub App installation token', result.stdout)
        self.assertEqual(self.read_state()['calls'], [])

    def test_access_check_lists_rulesets_and_resolves_teams_without_writes(self):
        result = self.run_sync(args=('--check-access',))
        self.assertIn('no writes performed', result.stdout)
        calls = self.read_state()['calls']
        self.assertIn(['GET', '/orgs/test-org/teams/owners-linux'], calls)
        self.assertIn(['GET', '/orgs/test-org/rulesets'], calls)
        self.assertTrue(all(method == 'GET' for method, _ in calls))

    def test_same_name_tag_ruleset_is_not_repurposed(self):
        state = self.read_state()
        state['rulesets'] = {'8': {'id': 8, 'name': 'Require two approvals', 'target': 'tag'}}
        self.write_state(state)
        self.env['POLICY_FILE'] = str(self.policies / 'require-two-approvals.json')
        result = self.run_sync(success=False)
        self.assertIn('different target', result.stderr)
        self.assertTrue(all(method == 'GET' for method, _ in self.read_state()['calls']))

    def test_resolved_team_must_match_requested_slug(self):
        state = self.read_state()
        state['teams']['owners-linux']['slug'] = 'owners-other'
        self.write_state(state)
        result = self.run_sync(success=False)
        self.assertIn('Invalid team response', result.stderr)
        self.assertTrue(all(method == 'GET' for method, _ in self.read_state()['calls']))

    def test_missing_access_grant_and_wildcard_mapping_fail_before_writes(self):
        self.access.write_text('{"teams": {}}')
        self.run_sync(success=False)
        self.assertEqual(self.read_state()['calls'], [])
        (self.policies / 'owner-only-merge/repositories.json').write_text('{"*": "owners-linux"}')
        self.run_sync(success=False)
        self.assertEqual(self.read_state()['calls'], [])

    def test_sync_rejects_drift_in_update_only_response(self):
        state = self.read_state()
        state['drift'] = 'Owner-only merges for linux'
        self.write_state(state)
        result = self.run_sync(success=False)
        self.assertIn('Ruleset verification failed', result.stderr)

    def test_linux_only_enrollment_keeps_shared_baseline_and_merge_methods(self):
        review = json.loads((POLICIES / 'require-two-approvals.json').read_text())
        baseline = json.loads((POLICIES / 'protect-main-develop.json').read_text())
        self.assertEqual(review['conditions']['repository_name']['include'], ['linux'])
        self.assertEqual(baseline['conditions']['repository_name']['include'], ['~ALL'])
        self.assertEqual(review['conditions']['ref_name'], baseline['conditions']['ref_name'])
        self.assertEqual(review['rules'][0]['parameters']['required_approving_review_count'], 2)
        self.assertEqual(review['rules'][0]['parameters']['allowed_merge_methods'],
                         baseline['rules'][0]['parameters']['allowed_merge_methods'])
        self.assertEqual(json.loads((POLICIES / 'owner-only-merge/repositories.json').read_text()),
                         {'linux': 'owners-linux'})


if __name__ == '__main__':
    unittest.main()
