"""Exercise the native Priority API using this Actions job's GITHUB_TOKEN."""

import argparse
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import issue_triage as triage


def main():
    repo = triage.require_env('GITHUB_REPOSITORY')
    token = triage.require_env('GITHUB_TOKEN')
    issue = triage.api_request('POST', f'/repos/{repo}/issues', token, {
        'title': 'Automated Priority API verification',
        'body': 'Controlled test for sima-neat/core#1557. This issue is closed automatically after the check.',
    })
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'caller-repo/.github/issue-triage'
            config.mkdir(parents=True)
            (config / 'config.json').write_text(json.dumps({
                'automation': {'apply_labels': False, 'post_comment': False, 'apply_priority': True},
                'priority_field_id': 39559438,
            }))
            event = root / 'event.json'
            event.write_text(json.dumps({'action': 'opened', 'issue': {'number': issue['number']}}))
            proposal = root / 'proposal.json'
            proposal.write_text(json.dumps({
                'category': 'ci', 'labels': [], 'priority': 'Medium',
                'priority_reason': 'Controlled verification of issue-field permissions.',
                'public_comment': '',
            }))
            with patch.dict(os.environ, {
                'GITHUB_WORKSPACE': str(root), 'GITHUB_EVENT_PATH': str(event),
                'GITHUB_EVENT_NAME': 'issues', 'GITHUB_RUN_ATTEMPT': '1',
            }):
                triage.apply_proposal(argparse.Namespace(
                    issue_number=issue['number'], repo_triage_path='.github/issue-triage',
                    proposal=str(proposal), dry_run='false',
                ))
            print(f"Verified Priority=Medium using the workflow token on {issue['html_url']}")
    finally:
        triage.api_request('PATCH', f"/repos/{repo}/issues/{issue['number']}", token, {'state': 'closed'})


if __name__ == '__main__':
    main()
