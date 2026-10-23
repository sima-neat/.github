import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

SPEC = importlib.util.spec_from_file_location('runner', Path(__file__).parents[1] / 'scripts/resolve_modalix_runner.py')
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
SOURCE_RUNNER = R
BUNDLE_SPEC = importlib.util.spec_from_file_location(
    'bundle', Path(__file__).parents[1] / 'scripts/bundle_modalix_resolver.py')
BUNDLE = importlib.util.module_from_spec(BUNDLE_SPEC)
BUNDLE_SPEC.loader.exec_module(BUNDLE)
EMBEDDED_SOURCE = BUNDLE.bundled_source(BUNDLE.WORKFLOW.read_text())
EMBEDDED_RUNNER = types.ModuleType('embedded_runner')
exec(compile(EMBEDDED_SOURCE, str(BUNDLE.WORKFLOW), 'exec'), EMBEDDED_RUNNER.__dict__)
VERSION = '3.0.0_master_B2263'
LABELS = ['self-hosted', 'Linux', 'ARM64', 'modalix', 'modalix-som-16gig', 'vulcan-prod']
MANIFEST = {'platform-version': '3.0.0', 'elxr_version': VERSION}


@pytest.fixture(autouse=True, params=[SOURCE_RUNNER, EMBEDDED_RUNNER], ids=['source', 'bundled'])
def runner_implementation(request, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], 'R', request.param)


def test_workflow_bundle_matches_reviewed_source():
    assert EMBEDDED_SOURCE == BUNDLE.SOURCE.read_text()


@pytest.mark.parametrize('platform,legacy,prefix,expected', [
    ('2.0.0', 'modalix-devkit', 'modalix-devkit-', 'modalix-devkit'),
    ('3.0.0', 'modalix-devkit', 'modalix-devkit-', 'modalix-devkit-3.0.0'),
    ('2.0.0', 'custom-legacy', 'custom-', 'custom-legacy'),
    ('3.0.0', 'custom-legacy', 'custom-', 'custom-3.0.0'),
])
def test_legacy_entrypoint_without_credentials_or_shared_checkout(tmp_path, platform, legacy, prefix, expected):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'platform-version': platform}))
    output = tmp_path / 'output'
    env = dict(os.environ, MANIFEST_PATH=str(manifest), GITHUB_OUTPUT=str(output),
               GITHUB_REPOSITORY='sima-neat/core', RUNNER_DISCOVERY_TOKEN='',
               RUNNER_LABELS=json.dumps(LABELS), LEGACY_PLATFORM_VERSION='2.0.0',
               LEGACY_RUNNER_GROUP=legacy, VERSIONED_RUNNER_GROUP_PREFIX=prefix,
               REPOSITORY_PRIVATE='true', ALLOW_VERSION_FALLBACK='false')
    source = EMBEDDED_SOURCE if R is EMBEDDED_RUNNER else BUNDLE.SOURCE.read_text()
    subprocess.run([sys.executable, '-'], input=source, text=True, env=env,
                   cwd=tmp_path, check=True, capture_output=True)
    outputs = dict(line.split('=', 1) for line in output.read_text().splitlines())
    assert outputs['modalix_runner_group'] == expected
    assert outputs['platform_version'] == platform
    assert outputs['elxr_version'] == ''
    assert outputs['version_fallback'] == 'false'
    assert json.loads((tmp_path / 'runner-resolution.json').read_text())['candidate_runners'] == []


def runner(name='new', version=VERSION, *, status='online', busy=False, labels=LABELS):
    return dict(name=name, status=status, busy=busy, labels=[{'name': v} for v in labels + [version]])


def inventory(runners, *, visibility='all', repositories=None):
    def get(path):
        if '/repositories?' in path:
            return {'repositories': repositories or []}
        if '/runners?' in path:
            assert '/7/runners?' in path
            return {'runners': runners}
        return {'runner_groups': [{'name': 'modalix-devkit-3.0.0', 'id': 7, 'visibility': visibility}]}
    return get


def resolve(runners, **kwargs):
    return R.resolve(MANIFEST, LABELS, inventory(runners), 'sima-neat/llima', **kwargs)


def test_exact_match_preferred_even_when_busy_and_fallback_allowed():
    result = resolve([runner('old', '3.0.0_develop_B2121'), runner(busy=True)], allow_fallback=True)
    assert result['runner_labels'] == LABELS + [VERSION]
    assert result['candidate_runners'] == ['new']
    assert result['version_fallback'] is False


def test_fallback_preserves_every_other_label():
    result = resolve([runner('old', '3.0.0_develop_B2121')], allow_fallback=True)
    assert result['runner_labels'] == LABELS
    assert result['version_fallback'] is True


@pytest.mark.parametrize('runners', [[], [runner(version='3.0.0_master_B226')], [runner(status='offline')], [runner(labels=LABELS[:-1])]])
def test_no_exact_match_fails_by_default(runners):
    with pytest.raises(ValueError, match='No online runner matches firmware'):
        resolve(runners)


def test_fallback_cannot_bypass_hardware_environment_or_online_requirement():
    with pytest.raises(ValueError, match='hardware/environment'):
        resolve([runner(labels=LABELS[:-1]), runner(status='offline')], allow_fallback=True)


def test_api_failure_is_not_fallback():
    def denied(path):
        raise ValueError('HTTP 403')
    with pytest.raises(ValueError, match='403'):
        R.resolve(MANIFEST, LABELS, denied, 'sima-neat/llima', allow_fallback=True)


def test_selected_group_must_allow_caller_repository():
    with pytest.raises(ValueError, match='has no access'):
        R.resolve(MANIFEST, LABELS, inventory([runner()], visibility='selected'), 'sima-neat/llima')
    result = R.resolve(MANIFEST, LABELS, inventory([runner()], visibility='selected',
                       repositories=[{'full_name': 'sima-neat/llima'}]), 'sima-neat/llima')
    assert result['runner_labels'][-1] == VERSION


def test_pagination_reaches_match_beyond_first_page():
    def get(path):
        page = int(path.split('page=')[-1])
        return {'runners': [runner('old', '3.0.0_master_B1')] * 100 if page == 1 else [runner()]}
    runners = R.collect(get, '/runners', 'runners')
    assert len(runners) == 101
    assert R.select(runners, LABELS, VERSION, False)[2][0]['name'] == 'new'


@pytest.mark.parametrize('version', ['', '3.0.0', '3.0.0_master_B2263\n', '2.0.0_master_B2263'])
def test_invalid_or_mismatched_version_fails(version):
    with pytest.raises(ValueError):
        R.resolve(dict(MANIFEST, elxr_version=version), LABELS, inventory([]), 'sima-neat/llima')


@pytest.mark.parametrize('platform,group', [('2.0.0', 'modalix-devkit'), ('3.0.0', 'modalix-devkit-3.0.0'), (' 3.0.0 ', 'modalix-devkit-3.0.0')])
def test_legacy_manifest_preserves_group_without_inventory(platform, group):
    result = R.resolve({'platform-version': platform}, LABELS, None, 'sima-neat/llima')
    assert result['modalix_runner_group'] == group
    assert result['runner_labels'] == LABELS


def test_case_insensitive_github_labels():
    assert resolve([runner(version=VERSION.lower(), labels=[v.upper() for v in LABELS])])['candidate_runners'] == ['new']


def test_group_mismatch_never_uses_other_groups():
    def get(path):
        return {'runner_groups': [{'id': 1, 'name': 'modalix-devkit-2.0.0', 'visibility': 'all'}]}
    with pytest.raises(ValueError, match='Expected one accessible runner group'):
        R.resolve(MANIFEST, LABELS, get, 'sima-neat/llima', allow_fallback=True)


def test_public_repository_requires_group_opt_in():
    with pytest.raises(ValueError, match='does not allow public'):
        R.resolve(MANIFEST, LABELS, inventory([runner()]), 'sima-neat/llima', repository_private=False)



@pytest.mark.parametrize('labels', [[], 'modalix', [''], LABELS + [VERSION]])
def test_reject_invalid_or_conflicting_caller_labels(labels):
    with pytest.raises(ValueError):
        R.resolve(MANIFEST, labels, inventory([runner()]), 'sima-neat/llima')


def test_discovery_needs_only_organization_runner_endpoints():
    paths = []
    base = inventory([runner()])
    def get(path):
        paths.append(path)
        assert path.startswith('/orgs/sima-neat/actions/runner-groups')
        return base(path)
    assert R.resolve(MANIFEST, LABELS, get, 'sima-neat/internals')['candidate_runners'] == ['new']
    assert len(paths) == 2


@pytest.mark.parametrize('options', [{'legacy_version': ''}, {'legacy_version': '  '}, {'legacy_group': ''}, {'prefix': ''}])
def test_empty_legacy_configuration_is_rejected(options):
    with pytest.raises(ValueError, match='must not be empty'):
        R.resolve({'platform-version': '2.0.0'}, LABELS, None, 'sima-neat/llima', **options)
