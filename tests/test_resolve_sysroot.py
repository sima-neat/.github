import gzip
import hashlib
import importlib.util
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location('resolver', Path(__file__).parents[1] / 'scripts/resolve_sysroot.py')
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def mirror(indexes, archives=None, *, corrupt_index=False):
    calls = []
    def fetch(url):
        calls.append(url)
        if '/pool/' in url:
            return (archives or {})[url.rsplit('/', 1)[1]]
        channel = url.split(R.MIRROR + '/', 1)[1].split('/', 1)[0]
        text = '\n\n'.join('\n'.join(f'{k}: {v}' for k, v in fields.items()) for fields in indexes.get(channel, []))
        data = gzip.compress(text.encode())
        digest = hashlib.sha256(data).hexdigest()
        if url.endswith('/Release'):
            return f'SHA256:\n {digest} {len(data)} {R.INDEX}\n'.encode()
        assert url.endswith('/by-hash/SHA256/' + digest)
        return b'corrupt' if corrupt_index else data
    return fetch, calls


def palette(version):
    return dict(Package=R.PACKAGE, Version=version, Architecture='all',
                Filename='pool/palette.deb', Size='5', SHA256=hashlib.sha256(b'other').hexdigest())


def base_files(version, archive):
    return dict(Package='simaai-base-files-modalix', Version=version, Architecture='arm64',
                Filename='pool/base.deb', Size=str(len(archive)), SHA256=hashlib.sha256(archive).hexdigest())


def deb(tmp_path, identity):
    stage = tmp_path / 'stage'
    (stage / 'etc').mkdir(parents=True)
    (stage / 'DEBIAN').mkdir()
    (stage / 'etc/packageinfo').write_text(identity)
    (stage / 'DEBIAN/control').write_text('Package: simaai-base-files-modalix\nVersion: 3.1.0\nArchitecture: arm64\nMaintainer: Test <test@example.invalid>\nDescription: test fixture\n')
    output = tmp_path / 'base.deb'
    subprocess.run(['dpkg-deb', '--build', str(stage), str(output)], check=True, capture_output=True)
    return output.read_bytes()


@pytest.mark.parametrize('firmware,version,channel', [
    ('3.0.0_master_B2263', '3.0.0~pre2263', 'pre-release'),
    ('3.0.0_develop_B2121', '3.0.0~git202610140145.e5ece50-2121', 'daily'),
])
def test_resolves_unique_cohort_using_verified_by_hash(firmware, version, channel):
    fetch, calls = mirror({channel: [palette(version)]})
    result = R.resolve(firmware, fetch)
    assert result['sysroot_version'] == version
    assert result['channel'] == channel
    assert not any(url.endswith('/Packages.gz') for url in calls)


@pytest.mark.parametrize('versions', [[], ['3.0.0~pre2121'], [
    '3.0.0~git202610140145.e5ece50-2121', '3.0.0~git202610140145.abcdef0-2121']])
def test_missing_wrong_channel_or_ambiguous_cohort_fails(versions):
    fetch, _ = mirror({'daily': [palette(v) for v in versions]})
    with pytest.raises(ValueError, match='expected one'):
        R.resolve('3.0.0_develop_B2121', fetch)


def test_checksum_mismatch_fails_without_flat_index_fallback():
    fetch, calls = mirror({'pre-release': [palette('3.0.0~pre2263')]}, corrupt_index=True)
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


@pytest.mark.parametrize('base,build', [('3.0.0', '9001'), ('3.1.0', '9012'), ('3.2.0', '9023')])
def test_release_mapping_is_discovered_from_payload_not_build_constants(tmp_path, base, build):
    firmware = f'{base}_master_B{build}'
    archive = deb(tmp_path, f'SIMA_PACKAGE_VERSION = {firmware}\n')
    version = base + '+release2' if base == '3.2.0' else base
    fields = base_files(version, archive)
    fetch, _ = mirror({'release': [palette(version), fields]}, {'base.deb': archive})
    result = R.resolve(firmware, fetch)
    assert result['sysroot_version'] == version
    assert result['channel'] == 'release'
    assert result['release_packages']['simaai-base-files-modalix']['SHA256'] == fields['SHA256']
    with pytest.raises(ValueError, match='expected one'):
        R.resolve(f'{base}_master_B9999', fetch)


def test_release_corruption_and_cross_channel_ambiguity_fail(tmp_path):
    firmware = '3.0.0_master_B9001'
    archive = deb(tmp_path, f'SIMA_PACKAGE_VERSION = {firmware}\n')
    release = [palette('3.0.0'), base_files('3.0.0', archive)]
    fetch, _ = mirror({'release': release}, {'base.deb': b'corrupt'})
    with pytest.raises(ValueError, match='SHA256'):
        R.resolve(firmware, fetch)
    fetch, _ = mirror({'release': release, 'pre-release': [palette('3.0.0~pre9001')]}, {'base.deb': archive})
    with pytest.raises(ValueError, match='found 2'):
        R.resolve(firmware, fetch)


@pytest.mark.parametrize('identity', ['', 'SIMA_PACKAGE_VERSION = 3.0.0_master_B9001\nSIMA_PACKAGE_VERSION = 3.0.0_master_B9002\n'])
def test_payload_requires_one_firmware_identity(tmp_path, identity):
    with pytest.raises(ValueError, match='exactly one'):
        R.package_firmware(deb(tmp_path, identity))


def test_release_index_rejects_missing_ambiguous_or_unsafe_base_files(tmp_path):
    archive = deb(tmp_path, 'SIMA_PACKAGE_VERSION = 3.0.0_master_B9001\n')
    fields = base_files('3.0.0', archive)
    for records in ([palette('3.0.0')], [palette('3.0.0'), fields, fields],
                    [palette('3.0.0'), dict(fields, Filename='pool/../../secret')]):
        fetch, _ = mirror({'release': records}, {'base.deb': archive})
        with pytest.raises(ValueError):
            R.resolve('3.0.0_master_B9001', fetch)
