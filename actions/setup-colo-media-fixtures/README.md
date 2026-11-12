# Set up Colo media fixtures

Call this composite action before the test steps in a job running on a
Kerrigan-managed DevKit with Python 3. Pin the action to a commit SHA.

```yaml
- name: Set up Colo media fixtures
  uses: sima-neat/.github/actions/setup-colo-media-fixtures@<commit-sha>
  with:
    profile: all
```

The action reads `http.discovery_url` from `/etc/colo-services.json` and fetches
the catalog once. It writes the existing fixture variables to `GITHUB_ENV`
so subsequent test steps can read them.

| Catalog group | Variable prefix |
| --- | --- |
| `h264-rtsp` | `SIMANEAT_TEST_RTSP_H264` |
| `h265-rtsp` | `SIMANEAT_TEST_RTSP_H265` |
| `mjpeg-rtsp` | `SIMANEAT_TEST_RTSP_MJPEG` |
| `mjpeg-http` | `SIMANEAT_TEST_HTTP_MJPEG` |

For each enabled group, `_URL` is the first URL and `_URLS` is the full
comma-separated list in catalog order. `_FPS` comes from the first URL's
metadata when the rate is a positive integer. MJPEG requires that rate.
A missing required group or invalid fixture fails setup before exporting values.

The `all` profile is the default. Use `http` for HTTP MJPEG only or `none`
for jobs without media fixtures. The optional `config-path` input overrides
the path to the Colo service configuration.
