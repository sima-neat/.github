import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('runner', Path(__file__).parents[1] / 'scripts/resolve_modalix_runner.py')
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
VERSION = '3.0.0_master_B2263'
LABELS = ['self-hosted', 'Linux', 'ARM64', 'modalix', 'modalix-som-16gig', 'vulcan-prod']
MANIFEST = {'platform-version': '3.0.0', 'elxr_version': VERSION}


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


def test_legacy_manifest_preserves_group_without_inventory():
    result = R.resolve({'platform-version': '2.0.0'}, LABELS, None, 'sima-neat/llima')
    assert result['modalix_runner_group'] == 'modalix-devkit'
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
