import importlib.util
import json
from pathlib import Path
import subprocess
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('failure_analysis', ROOT / 'scripts/failure_analysis.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def report():
    return dict(summary='Docs failed <!channel>', likely_cause='Duplicate sidebar key',
                evidence='Build docs: duplicate category', next_action='Give categories unique keys', confidence='high')


def test_attempt_specific_collection_and_missing_logs():
    calls = []
    def api(path, raw=False):
        calls.append(path)
        if path.endswith('/attempts/2'):
            return dict(repository={'full_name': 'sima-neat/core'}, status='completed', head_sha='abc')
        if '/jobs?' in path:
            return {'jobs': [dict(id=1, name='docs', conclusion='failure', steps=[]), dict(id=2, conclusion='success')]}
        raise subprocess.CalledProcessError(1, 'gh')
    with patch.object(m, 'gh', api):
        result = m.collect('sima-neat/core', 123, 2)
    assert '/attempts/2/jobs?' in calls[1]
    assert len(result['jobs']) == 1
    assert 'unavailable' in result['jobs'][0]['log']
    assert result['run_attempt'] == 2


def test_log_truncation_and_redaction():
    def api(path, raw=False):
        if raw:
            return 'Authorization: Bearer secret\n' + 'x' * 30000 + '\nxoxb-123-secret'
        if '/jobs?' in path:
            return {'jobs': [dict(id=1, name='docs', conclusion='failure', steps=[])]}
        return dict(repository={'full_name': 'sima-neat/core'}, status='completed')
    with patch.object(m, 'gh', api):
        log = m.collect('sima-neat/core', 1, 1)['jobs'][0]['log']
    assert 'secret' not in log
    assert '[LOG TRUNCATED]' in log
    assert len(log) < 24100


def test_thread_payload_keeps_generated_content_plain():
    result = m.payload(report(), 'C123', '123.456', 'sima-neat/core', 123, 2)
    assert result['thread_ts'] == '123.456'
    assert result['reply_broadcast'] is False
    assert all(b['text']['type'] == 'plain_text' for b in result['blocks'][:-1])


@pytest.mark.parametrize('bad', [{}, {**report(), 'confidence': 'certain'}, {**report(), 'summary': 'x' * 1801}])
def test_reject_malformed_analysis(bad):
    with pytest.raises(ValueError):
        m.validate(bad)


@pytest.mark.parametrize('channel,ts', [('C123', ''), ('', '1.2'), ('C123', 'bad')])
def test_never_posts_without_parent(channel, ts):
    with pytest.raises(ValueError):
        m.payload(report(), channel, ts, 'sima-neat/core', 1, 1)


def test_analyzer_has_no_github_or_slack_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('GH_TOKEN', 'private')
    monkeypatch.setenv('SLACK_BOT_TOKEN', 'private')
    output = tmp_path / 'analysis.json'
    def run(cmd, **kwargs):
        assert 'GH_TOKEN' not in kwargs['env']
        assert 'SLACK_BOT_TOKEN' not in kwargs['env']
        assert '--sandbox' in cmd and 'read-only' in cmd
        assert '--ephemeral' in cmd
        assert kwargs['timeout'] == 480
        output.write_text(json.dumps(report()))
    with patch.object(m.subprocess, 'run', run):
        m.analyze({}, ROOT / 'schemas/failure-analysis.schema.json', output)
    assert json.loads(output.read_text())['confidence'] == 'high'


def test_new_gh_escape_guard_is_retried_and_captured():
    guarded = subprocess.CompletedProcess([], 1, b'', b'pass --allow-escape-sequences')
    allowed = subprocess.CompletedProcess([], 0, b'\x1b[31merror', b'')
    with patch.object(m.subprocess, 'run', side_effect=[guarded, allowed]) as run:
        assert m.gh('repos/a/b/actions/jobs/1/logs', raw=True) == '\x1b[31merror'
    assert run.call_args.args[0][-1] == '--allow-escape-sequences'


def test_pr_checks_never_schedule_persistent_runners():
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/test-failure-analysis.yml').read_text())
    # PyYAML's YAML 1.1 loader treats the Actions `on` key as True.
    assert set(workflow[True]) == {'pull_request'}
    assert workflow['jobs']
    assert all(job['runs-on'] == 'ubuntu-latest' for job in workflow['jobs'].values())


def test_bridge_smoke_accepts_only_exact_protected_main_push():
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/smoke-failure-analysis.yml').read_text())
    assert set(workflow[True]) == {'push'}
    assert workflow[True]['push']['branches'] == ['main']
    job = workflow['jobs']['bridge-smoke']
    assert job['if'] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    checkout = next(step for step in job['steps'] if step.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with']['ref'] == '${{ github.sha }}'
    assert checkout['with']['persist-credentials'] is False
