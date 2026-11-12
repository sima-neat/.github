"""Resolve the stream fixture environment for a GitHub Actions DevKit job."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

GROUPS = {
    "h264-rtsp": ("SIMANEAT_TEST_RTSP_H264", {"rtsp"}),
    "h265-rtsp": ("SIMANEAT_TEST_RTSP_H265", {"rtsp"}),
    "mjpeg-rtsp": ("SIMANEAT_TEST_RTSP_MJPEG", {"rtsp"}),
    "mjpeg-http": ("SIMANEAT_TEST_HTTP_MJPEG", {"http", "https"}),
}


def checked_url(value, schemes, label):
    if not isinstance(value, str) or "," in value or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError(f"{label} contains an invalid URL")
    parsed = urlsplit(value)
    if parsed.scheme not in schemes or not parsed.hostname or parsed.username is not None:
        raise ValueError(f"{label} contains an invalid URL")
    if parsed.port == 0:
        raise ValueError(f"{label} contains an invalid port")
    return value


def fixture_environment(catalog, profile):
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ValueError("Expected media catalog schema version 1")
    links = catalog.get("links")
    sources = catalog.get("sources")
    if not isinstance(links, dict) or not isinstance(sources, list):
        raise ValueError("Expected the media catalog to contain links and sources")

    fps_by_url = {source["url"]: source.get("fps") for source in sources}
    environment = {}
    groups = GROUPS if profile == "all" else ("mjpeg-http",)
    for group in groups:
        prefix, schemes = GROUPS[group]
        urls = links.get(group)
        if not isinstance(urls, list) or not urls:
            raise ValueError(f"Media catalog has no URLs for required group {group}")
        urls = [checked_url(url, schemes, group) for url in urls]
        environment[f"{prefix}_URL"] = urls[0]
        environment[f"{prefix}_URLS"] = ",".join(urls)

        fps = fps_by_url.get(urls[0])
        if type(fps) in (int, float) and fps > 0 and fps % 1 == 0:
            environment[f"{prefix}_FPS"] = str(int(fps))
        elif group.startswith("mjpeg-"):
            raise ValueError(f"The first {group} source must advertise a positive integer FPS")
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", choices=("none", "http", "all"), required=True)
    args = parser.parse_args()
    if args.profile == "none":
        return

    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or config.get("schema_version") != 1:
            raise ValueError("Expected Colo service configuration schema version 1")
        http = config.get("http")
        if not isinstance(http, dict) or not http.get("discovery_url"):
            raise ValueError("Missing http.discovery_url in Colo service configuration")
        discovery_url = checked_url(http["discovery_url"], {"http", "https"}, "http.discovery_url")
        with urlopen(discovery_url, timeout=10) as response:
            catalog = json.load(response)
        environment = fixture_environment(catalog, args.profile)
        with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as output:
            output.write("".join(f"{key}={value}\n" for key, value in environment.items()))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Colo media setup failed ({args.config}, profile {args.profile}): {error}\n")


if __name__ == "__main__":
    main()
