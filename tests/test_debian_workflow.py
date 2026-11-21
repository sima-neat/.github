"""Check the executable workflow/action contract, including the deployed revision."""

import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class DebianWorkflowContractTest(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load((ROOT / '.github/workflows/vulcan-publish-debian.yml').read_text())
        self.action = yaml.safe_load((ROOT / 'debian-publish/action.yml').read_text())
        self.steps = self.workflow['jobs']['publish']['steps']
        self.calls = [step for step in self.steps if step.get('id') in {'validate', 'submit'}]

    def test_action_uses_supported_identical_commit_pins(self):
        self.assertEqual(len(self.calls), 2)
        pins = [step['uses'] for step in self.calls]
        self.assertEqual(pins[0], pins[1])
        self.assertRegex(pins[0], r'^sima-neat/\.github/debian-publish@[0-9a-f]{40}$')

    def test_pinned_action_matches_tested_source(self):
        ref = self.calls[0]['uses'].rsplit('@', 1)[1]
        self.assertRegex(ref, r'^[0-9a-f]{40}$')
        paths = subprocess.check_output(
            ['git', 'ls-tree', '-r', '--name-only', ref, '--', 'debian-publish/'],
            cwd=ROOT, text=True,
        ).splitlines()
        local = sorted(str(path.relative_to(ROOT)) for path in (ROOT / 'debian-publish').rglob('*')
                       if path.is_file() and '__pycache__' not in path.parts)
        self.assertEqual(sorted(paths), local)
        for path in paths:
            with self.subTest(path=path):
                pinned = subprocess.check_output(['git', 'show', f'{ref}:{path}'], cwd=ROOT)
                self.assertEqual(pinned, (ROOT / path).read_bytes(),
                                 'Commit action changes and update both workflow pins before pushing')

    def test_action_inputs_and_outputs_match_workflow(self):
        defined = self.action['inputs']
        required = {key for key, value in defined.items() if value.get('required') and 'default' not in value}
        for step in self.calls:
            with self.subTest(step=step['id']):
                supplied = step['with']
                self.assertFalse(set(supplied) - set(defined))
                self.assertFalse(required - set(supplied))
                self.assertEqual(supplied['mode'], step['id'])
        outputs = self.workflow['jobs']['publish']['outputs']
        self.assertEqual(set(outputs), set(self.action['outputs']))
        for key, value in outputs.items():
            self.assertEqual(value, '${{ steps.submit.outputs.' + key + ' }}')
        env = self.action['runs']['steps'][0]['env']
        self.assertEqual(set(env), {key.upper() for key in defined})
        for key in defined:
            self.assertEqual(env[key.upper()], '${{ inputs.' + key + ' }}')

    def test_validation_precedes_credentials_and_submission(self):
        validate = next(i for i, step in enumerate(self.steps) if step.get('id') == 'validate')
        submit = next(i for i, step in enumerate(self.steps) if step.get('id') == 'submit')
        credentials = next(i for i, step in enumerate(self.steps)
                           if step.get('uses', '').startswith('aws-actions/configure-aws-credentials@'))
        self.assertLess(validate, credentials)
        self.assertLess(credentials, submit)
        self.assertEqual(self.workflow['permissions'], {'actions': 'read', 'id-token': 'write'})


if __name__ == '__main__':
    unittest.main()
