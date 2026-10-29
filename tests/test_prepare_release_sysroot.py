import json
import hashlib
import importlib.util
from pathlib import Path
import pytest

SPEC = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'scripts/prepare_release_sysroot.py')
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def test_release_adapter_preserves_transaction_and_exact_revision_admission():
    source = '''configure_agate_update_apt
validate_staged_sysroot
"${DIRECTORY_HELPER}" activate
    if [[ "${args[0]}" == *~git* ]]; then
      channel=daily
'''
    updated = R.adapt_updater(source, '3.1.0')
    assert '"${args[0]}" == 3.1.0' in updated
    assert 'channel=release' in updated
    assert 'https://debian.neat.sima.ai/release' in updated
    assert source.splitlines()[:3] == updated.splitlines()[:3]
    for invalid in [source.replace('validate_staged_sysroot', ''), source * 2, '']:
        with pytest.raises(ValueError):
            R.adapt_updater(invalid, '3.1.0')


def test_release_components_keep_upstream_versions_from_isolated_release_source():
    source = '''        if stable and any(origin.site == "repo.sima.ai" and origin.codename == "agate"
                          for origin in origins):
            if "~" not in candidate.version:
                daily.append(candidate)
        elif not stable:
            unchanged()
'''
    updated = R.adapt_downloader(source)
    assert 'if "~" not in candidate.version' not in updated
    assert 'elif not stable:\n            unchanged()' in updated
    assert '"debian.neat.sima.ai"' in updated
    with pytest.raises(ValueError):
        R.adapt_downloader('unexpected SDK')


def test_reject_wrong_package_inventory_and_wrong_firmware(tmp_path):
    resolution = {'elxr_version': '3.0.0_master_B2479', 'sysroot_version': '3.0.0',
                  'release_packages': {'simaai-base-files-modalix': {'SHA256': 'a' * 64, 'Size': '1'}}}
    root = tmp_path / 'root'
    (root / 'var/lib/sima-sdk').mkdir(parents=True)
    (root / 'etc').mkdir()
    (root / 'var/lib/sima-sdk/sysroot-packages.tsv').write_text('simaai-palette-modalix\tall\t3.0.0~pre2479\t/usr\n')
    with pytest.raises(ValueError, match='inventory'):
        R.verify(root, tmp_path, resolution)
    (root / 'var/lib/sima-sdk/sysroot-packages.tsv').write_text('simaai-palette-modalix\tall\t3.0.0\t/usr\n')
    (root / 'etc/packageinfo').write_text('SIMA_PACKAGE_VERSION = 3.0.0_master_B2480\n')
    with pytest.raises(ValueError, match='packageinfo'):
        R.verify(root, tmp_path, resolution)
    (root / 'etc/packageinfo').write_text('SIMA_PACKAGE_VERSION = 3.0.0_master_B2479\n')
    with pytest.raises(ValueError, match='archive'):
        R.verify(root, tmp_path, resolution)


def test_resolution_is_bound_to_requested_manifest_identity(tmp_path):
    package = {'Version': '3.1.0', 'Architecture': 'arm64', 'Size': '1', 'SHA256': 'a' * 64}
    value = {'elxr_version': '3.1.0_master_B9001', 'sysroot_version': '3.1.0',
             'channel': 'release', 'package': 'simaai-palette-modalix',
             'index_sha256': 'a' * 64,
             'index_url': R.REPOSITORY + '/dists/agate/non-free/binary-arm64/by-hash/SHA256/' + 'a' * 64,
             'release_packages': {'simaai-palette-modalix': package, 'simaai-base-files-modalix': package}}
    path = tmp_path / 'resolution.json'
    path.write_text(json.dumps(value))
    assert R.read_resolution(path, '3.1.0_master_B9001', '3.1.0') == value
    for change in ({'elxr_version': '3.1.0_master_B9002'}, {'sysroot_version': '3.0.0'},
                   {'channel': 'daily'}, {'index_url': 'https://untrusted.invalid'}, {'release_packages': {}}):
        path.write_text(json.dumps(dict(value, **change)))
        with pytest.raises(ValueError):
            R.read_resolution(path, '3.1.0_master_B9001', '3.1.0')


def test_verified_resolution_checks_both_downloaded_archives(tmp_path):
    root = tmp_path / 'root'
    (root / 'etc').mkdir(parents=True)
    (root / 'var/lib/sima-sdk').mkdir(parents=True)
    (root / 'etc/packageinfo').write_text('SIMA_PACKAGE_VERSION = 3.1.0_master_B9012\n')
    (root / 'var/lib/sima-sdk/sysroot-packages.tsv').write_text('simaai-palette-modalix\tall\t3.1.0\t/usr\n')
    packages = {}
    for name in ('simaai-palette-modalix', 'simaai-base-files-modalix'):
        data = name.encode()
        (tmp_path / (name + '.deb')).write_bytes(data)
        packages[name] = {'Size': str(len(data)), 'SHA256': hashlib.sha256(data).hexdigest()}
    resolution = {'elxr_version': '3.1.0_master_B9012', 'sysroot_version': '3.1.0', 'release_packages': packages}
    R.verify(root, tmp_path, resolution)
    (tmp_path / 'simaai-palette-modalix.deb').write_bytes(b'changed')
    with pytest.raises(ValueError, match='archive'):
        R.verify(root, tmp_path, resolution)


def test_shared_build_downloads_opt_in_receipt_before_preparation():
    workflow = (Path(__file__).parents[1] / '.github/workflows/vulcan-build.yml').read_text()
    assert 'sysroot_resolution_artifact:' in workflow
    assert "if: ${{ inputs.sysroot_resolution_artifact != '' }}" in workflow
    assert workflow.index('- name: Retain shared sysroot resolution evidence') < workflow.index('- name: Pre-build')
