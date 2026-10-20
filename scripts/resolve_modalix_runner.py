#!/usr/bin/env python3
"""Resolve an eligible runner group and exact firmware labels, never change devices."""
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def collect(get, path, key):
    items = []
    for page in range(1, 1001):
        response = get(f'{path}?per_page=100&page={page}')
        batch = response[key]
        if not isinstance(batch, list):
            raise ValueError(f'Invalid runner inventory response: {key}')
        items.extend(batch)
        if len(batch) < 100:
            return items
    raise ValueError('Runner inventory pagination exceeded limit')


def select(runners, labels, version, allow_fallback):
    required = {label.casefold() for label in labels}
    eligible = [r for r in runners if r['status'] == 'online' and required.issubset(
        {label['name'].casefold() for label in r['labels']})]
    # Busy runners still qualify: wait for the exact firmware instead of downgrading.
    exact = [r for r in eligible if version.casefold() in
             {label['name'].casefold() for label in r['labels']}]
    if exact:
        return labels + [version], False, exact
    if not allow_fallback:
        raise ValueError(f'No online runner matches firmware {version} and all requested labels')
    if not eligible:
        raise ValueError('No online runner matches the required hardware/environment labels')
    return labels, True, eligible


def resolve(manifest, labels, get, repository, *, allow_fallback=False,
            repository_private=True, legacy_version='2.0.0', legacy_group='modalix-devkit', prefix='modalix-devkit-'):
    platform = manifest.get('platform-version', '')
    if not isinstance(platform, str) or not re.fullmatch(r'[0-9][0-9A-Za-z._+-]*', platform):
        raise ValueError('Missing or invalid platform-version')
    legacy_version, legacy_group, prefix = (value.strip() for value in (legacy_version, legacy_group, prefix))
    if not legacy_version or not legacy_group or not prefix:
        raise ValueError('Legacy platform version, runner group and prefix must not be empty')
    if not isinstance(labels, list) or not labels or any(
        not isinstance(label, str) or not label.strip() or '\n' in label for label in labels
    ):
        raise ValueError('runner-labels must be a nonempty JSON array of label strings')
    if any(re.fullmatch(r'\d+\.\d+\.\d+_(master|develop)_B\d+', label) for label in labels):
        raise ValueError('Firmware labels must come from manifest elxr_version')
    group = legacy_group if platform == legacy_version else prefix + platform
    result = dict(platform_version=platform, modalix_runner_group=group,
                  runner_labels=labels, elxr_version='', version_fallback=False,
                  candidate_runners=[])
    # Preserve older callers that have not adopted the firmware manifest field.
    if 'elxr_version' not in manifest:
        return result
    version = manifest['elxr_version']
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+_(master|develop)_B[1-9]\d*', version):
        raise ValueError('Invalid elxr_version; expected 3.0.0_master_B2263 or a develop build')
    if version.split('_', 1)[0] != platform:
        raise ValueError('elxr_version does not match platform-version')
    owner = quote(repository.split('/')[0], safe='')
    base = f'/orgs/{owner}/actions/runner-groups'
    groups = [g for g in collect(get, base, 'runner_groups') if g['name'] == group]
    if len(groups) != 1:
        raise ValueError(f'Expected one accessible runner group named {group}')
    selected = groups[0]
    # Check repository access before treating a group member as schedulable.
    if not repository_private and not selected.get('allows_public_repositories', False):
        raise ValueError(f'Runner group {group} does not allow public repositories')
    if selected['visibility'] == 'selected':
        repos = collect(get, f'{base}/{selected["id"]}/repositories', 'repositories')
        if not any(r['full_name'].casefold() == repository.casefold() for r in repos):
            raise ValueError(f'Repository {repository} has no access to runner group {group}')
    runners = collect(get, f'{base}/{selected["id"]}/runners', 'runners')
    resolved_labels, fallback, candidates = select(runners, labels, version, allow_fallback)
    result.update(runner_labels=resolved_labels, elxr_version=version,
                  version_fallback=fallback, candidate_runners=[r['name'] for r in candidates])
    return result


def main():
    token = os.environ.get('RUNNER_DISCOVERY_TOKEN', '')
    api = os.environ.get('GITHUB_API_URL', 'https://api.github.com').rstrip('/')

    def get(path):
        if not token:
            raise ValueError('Configure App credentials (app_id/app_private_key) with organization self-hosted-runners read access, or RUNNER_DISCOVERY_TOKEN')
        request = Request(api + path, headers={
            'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            raise ValueError(f'Runner discovery API {path.split('?')[0]} returned HTTP {error.code}; check token permissions and group access') from None

    result = resolve(
        json.loads(Path(os.environ['MANIFEST_PATH']).read_text()),
        json.loads(os.environ['RUNNER_LABELS']), get, os.environ['GITHUB_REPOSITORY'],
        allow_fallback=os.environ.get('ALLOW_VERSION_FALLBACK', 'false') == 'true',
        repository_private=json.loads(os.environ['REPOSITORY_PRIVATE']),
        legacy_version=os.environ['LEGACY_PLATFORM_VERSION'],
        legacy_group=os.environ['LEGACY_RUNNER_GROUP'], prefix=os.environ['VERSIONED_RUNNER_GROUP_PREFIX'])
    Path('runner-resolution.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key, value in result.items():
            output.write(f'{key}={value if isinstance(value, str) else json.dumps(value)}\n')
    if result['version_fallback']:
        print('::warning::No eligible exact firmware match; explicitly allowed version fallback is active')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        print(f'Runner resolution failed: {error}', file=sys.stderr)
        sys.exit(1)
