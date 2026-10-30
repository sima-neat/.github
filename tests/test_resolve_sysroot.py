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
