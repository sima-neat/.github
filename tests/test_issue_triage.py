import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import issue_triage as triage


class IssueTriageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config_path = root / 'caller-repo/.github/issue-triage'
        self.config_path.mkdir(parents=True)
        self.config = {
            'labels': {
                'allowed': ['bug', 'feature', 'ci', 'needs-repro'],
                'categories': ['bug', 'feature', 'ci'],
                'managed': ['needs-repro'],
                'deprecated': ['triage', 'patch-candidate'],
            },
            'automation': {'apply_priority': True},
            'priority_field_id': 42,
        }
        self.proposal = {
            'category': 'bug', 'labels': ['bug'], 'confidence': 'high',
            'priority': 'Medium', 'priority_reason': 'A workaround is available.',
            'priority_evidence': '', 'needs_human_review': False,
            'public_comment': 'The failing command identifies the affected path.',
        }
        self.event_path = root / 'event.json'
        self.event_path.write_text(json.dumps({'action': 'opened', 'issue': {'number': 1}}))
        self.proposal_path = root / 'proposal.json'
        self.args = argparse.Namespace(
            issue_number=1, repo_triage_path='.github/issue-triage',
            proposal=str(self.proposal_path), dry_run='false',
        )
        self.env = patch.dict(os.environ, {
            'GITHUB_TOKEN': 'test-token', 'GITHUB_REPOSITORY': 'sima-neat/core',
            'GITHUB_WORKSPACE': str(root), 'GITHUB_EVENT_PATH': str(self.event_path),
            'GITHUB_EVENT_NAME': 'issues', 'GITHUB_RUN_ATTEMPT': '1',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.labels = {'feature', 'needs-repro', 'outside-managed-labels'}
        self.fields = []
        self.calls = []
        self.comments = []
        self.api = patch.object(triage, 'api_request', side_effect=self.request)
        self.api.start()
        self.addCleanup(self.api.stop)
        self.paginated = patch.object(triage, 'api_paginated', side_effect=self.pages)
        self.paginated.start()
        self.addCleanup(self.paginated.stop)

    def request(self, method, path, token, payload=None, **kwargs):
        self.calls.append((method, path, payload))
        if path.endswith('/issue-field-values'):
            if method == 'POST':
                value = payload['issue_field_values'][0]['value']
                self.fields = [{'issue_field_id': 42, 'value': 123,
                                'single_select_option': {'name': value}}]
            return self.fields
        if method == 'GET' and path.endswith('/issues/1'):
            return {'labels': [{'name': name} for name in self.labels]}
        if method == 'POST' and path.endswith('/labels'):
            self.labels.update(payload['labels'])
        if method == 'DELETE' and '/issues/1/labels/' in path:
            self.labels.discard(path.rsplit('/', 1)[1])
        if method == 'POST' and path.endswith('/comments'):
            self.comments.append({'id': 7, 'body': payload['body']})
        if method == 'GET' and 'state=all&labels=' in path:
            return []
        return {}

    def pages(self, path, token, **kwargs):
        if path.endswith('/comments'):
            return self.comments
        if path.endswith('/labels'):
            return [{'name': 'triage'}, {'name': 'patch-candidate'}]
        return [{'number': 1, 'title': 'A test issue',
                 'labels': [{'name': name} for name in self.labels]}]

    def apply(self):
        (self.config_path / 'config.json').write_text(json.dumps(self.config))
        self.proposal_path.write_text(json.dumps(self.proposal))
        triage.apply_proposal(self.args)

    def priority_writes(self):
        return [call for call in self.calls if call[0] == 'POST' and call[1].endswith('/issue-field-values')]

    def test_initial_assignment_and_managed_label_replacement(self):
        self.apply()
        self.assertEqual(len(self.priority_writes()), 1)
        self.assertEqual(self.labels, {'bug', 'outside-managed-labels'})
        self.assertIn(self.proposal['priority_reason'], self.comments[-1]['body'])

    def test_existing_priority_preserved(self):
        self.fields = [{'issue_field_id': 42, 'value': 9, 'single_select_option': {'name': 'High'}}]
        self.apply()
        self.assertEqual(self.priority_writes(), [])
        self.assertEqual(self.fields[0]['single_select_option']['name'], 'High')

    def test_edits_reopens_manual_dispatch_and_reruns_preserve_empty_priority(self):
        for name, action, attempt in [('issues', 'edited', '1'), ('issues', 'reopened', '1'),
                                      ('workflow_dispatch', 'opened', '1'), ('issues', 'opened', '2')]:
            with self.subTest(name=name, action=action, attempt=attempt):
                os.environ.update(GITHUB_EVENT_NAME=name, GITHUB_RUN_ATTEMPT=attempt)
                self.event_path.write_text(json.dumps({'action': action, 'issue': {'number': 1}}))
                self.calls.clear()
                self.apply()
                self.assertEqual(self.priority_writes(), [])
                self.assertEqual(self.fields, [])

    def test_high_requires_evidenced_bug_and_urgent_is_human_only(self):
        cases = [('feature', 'High', 'A new capability is required.', 'high', False, False),
                 ('bug', 'High', '', 'high', False, False),
                 ('bug', 'High', 'A release blocker is claimed.', 'low', True, False),
                 ('bug', 'High', 'The supplied command loses every frame.', 'high', False, True),
                 ('bug', 'Urgent', 'The release is unusable.', 'high', False, False)]
        for category, priority, evidence, confidence, human, assign in cases:
            with self.subTest(category=category, priority=priority, evidence=evidence):
                self.proposal.update(category=category, priority=priority, priority_evidence=evidence,
                                     confidence=confidence, needs_human_review=human)
                self.fields = []
                self.calls.clear()
                self.apply()
                self.assertEqual(bool(self.priority_writes()), assign)

    def test_missing_information_leaves_priority_empty(self):
        self.proposal['labels'].append('needs-repro')
        self.apply()
        self.assertEqual(self.priority_writes(), [])

    def test_low_confidence_leaves_priority_empty(self):
        self.proposal['confidence'] = 'low'
        self.apply()
        self.assertEqual(self.priority_writes(), [])

    def test_dry_run_has_no_mutations(self):
        self.args.dry_run = 'true'
        self.apply()
        self.assertTrue(all(method == 'GET' for method, _, _ in self.calls))
        self.assertEqual(self.fields, [])

    def test_legacy_repository_keeps_additive_labels_and_no_priority_requests(self):
        self.config['automation']['apply_priority'] = False
        self.config['labels'].pop('categories')
        self.apply()
        self.assertIn('feature', self.labels)
        self.assertIn('needs-repro', self.labels)
        self.assertFalse(any(path.endswith('/issue-field-values') for _, path, _ in self.calls))

    def test_unknown_category_preserves_primary_label(self):
        self.proposal.update(category='unknown', labels=['needs-repro'], priority='')
        self.apply()
        self.assertIn('feature', self.labels)

    def test_invalid_comment_does_not_write_priority(self):
        self.proposal['public_comment'] = {'unexpected': True}
        with self.assertRaises(SystemExit):
            self.apply()
        self.assertEqual(self.priority_writes(), [])

    def test_migration_preserves_release_annotation_and_other_labels(self):
        self.labels = {'bug', 'patch-candidate', 'outside-managed-labels'}
        (self.config_path / 'config.json').write_text(json.dumps(self.config))
        args = argparse.Namespace(repo_triage_path=self.args.repo_triage_path, assignments=None, dry_run='false')
        triage.migrate_labels(args)
        self.assertEqual(self.labels, {'bug', 'outside-managed-labels'})
        self.assertIn('patch-candidate', self.comments[-1]['body'])
        self.assertTrue(any(method == 'DELETE' and path.endswith('/labels/patch-candidate')
                            for method, path, _ in self.calls))

    def test_ambiguous_migration_requires_review_before_any_write(self):
        self.labels = {'bug', 'feature', 'triage'}
        (self.config_path / 'config.json').write_text(json.dumps(self.config))
        args = argparse.Namespace(repo_triage_path=self.args.repo_triage_path, assignments=None, dry_run='false')
        with self.assertRaisesRegex(SystemExit, 'assignments'):
            triage.migrate_labels(args)
        self.assertEqual(self.calls, [])

    def test_migration_dry_run_has_no_mutations(self):
        self.labels = {'bug', 'patch-candidate'}
        (self.config_path / 'config.json').write_text(json.dumps(self.config))
        triage.migrate_labels(argparse.Namespace(
            repo_triage_path=self.args.repo_triage_path, assignments=None, dry_run='true',
        ))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.comments, [])


if __name__ == '__main__':
    unittest.main()
