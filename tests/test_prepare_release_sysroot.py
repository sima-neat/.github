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
    updated = R.adapt_updater(source)
    assert '"${args[0]}" == 3.0.0' in updated
    assert 'channel=release' in updated
    assert 'https://debian.neat.sima.ai/release' in updated
    assert source.splitlines()[:3] == updated.splitlines()[:3]
    for invalid in [source.replace('validate_staged_sysroot', ''), source * 2, '']:
        with pytest.raises(ValueError):
            R.adapt_updater(invalid)


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
    root = tmp_path / 'root'
    (root / 'var/lib/sima-sdk').mkdir(parents=True)
    (root / 'etc').mkdir()
    (root / 'var/lib/sima-sdk/sysroot-packages.tsv').write_text('simaai-palette-modalix\tall\t3.0.0~pre2479\t/usr\n')
    with pytest.raises(ValueError, match='inventory'):
        R.verify(root, tmp_path)
    (root / 'var/lib/sima-sdk/sysroot-packages.tsv').write_text('simaai-palette-modalix\tall\t3.0.0\t/usr\n')
    (root / 'etc/packageinfo').write_text('SIMA_PACKAGE_VERSION = 3.0.0_master_B2480\n')
    with pytest.raises(ValueError, match='packageinfo'):
        R.verify(root, tmp_path)
    (root / 'etc/packageinfo').write_text('SIMA_PACKAGE_VERSION = 3.0.0_master_B2479\n')
    with pytest.raises(ValueError, match='archive'):
        R.verify(root, tmp_path)
