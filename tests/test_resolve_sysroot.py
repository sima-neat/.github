import gzip
import hashlib
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('resolver', Path(__file__).parents[1] / 'scripts/resolve_sysroot.py')
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def mirror(versions, *, corrupt=False):
    text = '\n\n'.join(f'Package: simaai-palette-modalix\nVersion: {v}\nArchitecture: all\n' for v in versions)
    data = gzip.compress(text.encode())
    digest = hashlib.sha256(data).hexdigest()
    calls = []
    def fetch(url):
        calls.append(url)
        if url.endswith('/Release'):
            return f'SHA256:\n {digest} {len(data)} {R.INDEX}\n'.encode()
        assert url.endswith('/by-hash/SHA256/' + digest)
        return b'corrupt' if corrupt else data
    return fetch, calls


@pytest.mark.parametrize('firmware,version,channel', [
    ('3.0.0_master_B2263', '3.0.0~pre2263', 'pre-release'),
    ('3.0.0_develop_B2121', '3.0.0~git202610140145.e5ece50-2121', 'daily'),
])
def test_resolves_unique_cohort_using_verified_by_hash(firmware, version, channel):
    fetch, calls = mirror([version, '3.0.0~pre1', '3.0.0~git202601010000.abcdef0-12'])
    result = R.resolve(firmware, fetch)
    assert result['sysroot_version'] == version
    assert result['channel'] == channel
    assert len(calls) == 2
    assert not any(url.endswith('/Packages.gz') for url in calls)


@pytest.mark.parametrize('versions', [[], ['3.0.0~pre2121'], [
    '3.0.0~git202610140145.e5ece50-2121', '3.0.0~git202610140145.abcdef0-2121']])
def test_missing_wrong_channel_or_ambiguous_cohort_fails(versions):
    fetch, _ = mirror(versions)
    with pytest.raises(ValueError, match='expected one'):
        R.resolve('3.0.0_develop_B2121', fetch)


def test_checksum_mismatch_fails_without_flat_index_fallback():
    fetch, calls = mirror(['3.0.0~pre2263'], corrupt=True)
    with pytest.raises(ValueError, match='SHA256'):
        R.resolve('3.0.0_master_B2263', fetch)
    assert len(calls) == 2


@pytest.mark.parametrize('value', ['', '3.0.0', '3.0.0_master_B2263\n', '$(id)', '3.0.0_release_B2263'])
def test_invalid_firmware_rejected(value):
    with pytest.raises(ValueError):
        R.firmware_identity(value)


def test_manifest_migration_and_legacy_compatibility():
    assert R.manifest_selection({'platform-version': '3.0.0', 'elxr_version': '3.0.0_master_B2263'}) == ('3.0.0_master_B2263', None)
    assert R.manifest_selection({'platform-version': '3.0.0', 'sysroot-version': '3.0.0~pre2263'}) == (None, '3.0.0~pre2263')
    for manifest in [
        {'elxr_version': '3.0.0_master_B2263', 'platform-version': '2.1.3'},
        {'elxr_version': '3.0.0_master_B2263', 'platform-version': '3.0.0', 'sysroot-version': '3.0.0~pre2263'},
        {'platform-version': '3.0.0'},
    ]:
        with pytest.raises(ValueError):
            R.manifest_selection(manifest)


def test_2499_sysroot_stays_2499_even_when_2501_packages_exist():
    firmware = '3.0.0_master_B2499'
    manifest = {'platform-version': '3.0.0', 'elxr_version': firmware}
    selected, legacy = R.manifest_selection(manifest)
    assert selected == firmware and legacy is None
    fetch, _ = mirror(['3.0.0~pre2499', '3.0.0~pre2501'])
    result = R.resolve(selected, fetch)
    assert result['elxr_version'] == firmware
    assert result['sysroot_version'] == '3.0.0~pre2499'


def release_manifest():
    return {'platform-version': '3.0.0', 'elxr_version': '3.0.0_master_B2499',
            'sdk': {'channel': 'develop', 'sysroot-version': '3.0.0'}}


def test_official_pin_avoids_development_index_and_preserves_sdk_identity():
    def no_network(url):
        pytest.fail('Official release pin must not search a development index: ' + url)
    result = R.resolve_manifest(release_manifest(), no_network)
    assert result == {'elxr_version': '3.0.0_master_B2499', 'sysroot_version': '3.0.0',
                      'channel': 'release', 'package': R.PACKAGE, 'source': 'release-sdk-manifest'}


@pytest.mark.parametrize('pin', [None, '', '3.0.1', '3.0.0~pre2499', '3.0.0\n', '$(id)', 300])
def test_malformed_or_conflicting_release_pin_fails_closed(pin):
    data = release_manifest()
    data['sdk']['sysroot-version'] = pin
    with pytest.raises(ValueError, match='official release'):
        R.resolve_manifest(data)


def test_release_pin_does_not_allow_develop_firmware_or_legacy_ambiguity():
    data = release_manifest()
    data['elxr_version'] = '3.0.0_develop_B2499'
    with pytest.raises(ValueError):
        R.resolve_manifest(data)
    data = release_manifest()
    data['sysroot-version'] = '3.0.0~pre2499'
    with pytest.raises(ValueError):
        R.resolve_manifest(data)
    data.pop('elxr_version')
    with pytest.raises(ValueError, match='require elxr_version'):
        R.resolve_manifest(data)


def test_no_release_pin_keeps_verified_development_resolution():
    data = release_manifest()
    data['sdk'].pop('sysroot-version')
    fetch, calls = mirror(['3.0.0~pre2499'])
    assert R.resolve_manifest(data, fetch)['sysroot_version'] == '3.0.0~pre2499'
    assert len(calls) == 2


def test_release_cli_emits_existing_workflow_outputs_receipt_and_summary(tmp_path, monkeypatch):
    import json
    import sys
    manifest = tmp_path / 'manifest.json'
    receipt = tmp_path / 'receipt.json'
    output = tmp_path / 'output'
    summary = tmp_path / 'summary'
    manifest.write_text(json.dumps(release_manifest()))
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    monkeypatch.setattr(sys, 'argv', ['resolve_sysroot.py', '--manifest', str(manifest), '--receipt', str(receipt)])
    monkeypatch.setattr(R, 'resolve', lambda *args: pytest.fail('Release CLI must not use development resolution'))
    R.main()
    assert json.loads(receipt.read_text())['sysroot_version'] == '3.0.0'
    assert output.read_text() == 'elxr_version=3.0.0_master_B2499\nsysroot_version=3.0.0\n'
    assert '3.0.0_master_B2499' in summary.read_text()
