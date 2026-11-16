import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SETUP = Path(__file__).resolve().parents[1] / "actions/setup-colo-media-fixtures/setup.py"


class ColoMediaFixturesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.config = directory / "colo-services.json"
        self.github_env = directory / "github.env"
        self.github_env.write_text("EXISTING=preserved\n", encoding="utf-8")
        self.requests = []
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                test.requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(test.catalog).encode("utf-8"))

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(stop)
        self.config.write_text(json.dumps({
            "schema_version": 1,
            "http": {"discovery_url": f"http://127.0.0.1:{server.server_port}/media-sources.json"},
        }), encoding="utf-8")
        links = {
            "h264-rtsp": ["rtsp://colo.example:8554/src0", "rtsp://colo.example:8554/src1"],
            "h265-rtsp": ["rtsp://colo.example:8554/src5"],
            "mjpeg-rtsp": ["rtsp://colo.example:8554/src10"],
            "mjpeg-http": ["http://colo.example:8088/src0.mjpeg", "http://colo.example:8088/src1.mjpeg"],
        }
        self.catalog = {
            "schema_version": 1,
            "links": links,
            "sources": [{"url": url, "fps": 25.0 if index == 0 else 29.97}
                        for urls in links.values() for index, url in enumerate(urls)],
        }

    def setup(self, profile):
        return subprocess.run(
            [sys.executable, str(SETUP), "--profile", profile, "--config", str(self.config)],
            env={**os.environ, "GITHUB_ENV": str(self.github_env)},
            capture_output=True, text=True, timeout=15,
        )

    def environment(self):
        return dict(line.split("=", 1) for line in self.github_env.read_text(encoding="utf-8").splitlines())

    def test_all_exports_url_lists_and_fps_from_the_first_url(self):
        result = self.setup("all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, ["/media-sources.json"])
        self.assertEqual(self.environment(), {
            "EXISTING": "preserved",
            "SIMANEAT_TEST_RTSP_H264_URL": "rtsp://colo.example:8554/src0",
            "SIMANEAT_TEST_RTSP_H264_URLS": "rtsp://colo.example:8554/src0,rtsp://colo.example:8554/src1",
            "SIMANEAT_TEST_RTSP_H264_FPS": "25",
            "SIMANEAT_TEST_RTSP_H265_URL": "rtsp://colo.example:8554/src5",
            "SIMANEAT_TEST_RTSP_H265_URLS": "rtsp://colo.example:8554/src5",
            "SIMANEAT_TEST_RTSP_H265_FPS": "25",
            "SIMANEAT_TEST_RTSP_MJPEG_URL": "rtsp://colo.example:8554/src10",
            "SIMANEAT_TEST_RTSP_MJPEG_URLS": "rtsp://colo.example:8554/src10",
            "SIMANEAT_TEST_RTSP_MJPEG_FPS": "25",
            "SIMANEAT_TEST_HTTP_MJPEG_URL": "http://colo.example:8088/src0.mjpeg",
            "SIMANEAT_TEST_HTTP_MJPEG_URLS": "http://colo.example:8088/src0.mjpeg,http://colo.example:8088/src1.mjpeg",
            "SIMANEAT_TEST_HTTP_MJPEG_FPS": "25",
        })

    def test_http_only_accepts_empty_rtsp_groups(self):
        self.catalog["links"]["h264-rtsp"] = []
        result = self.setup("http")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(self.environment()), {
            "EXISTING", "SIMANEAT_TEST_HTTP_MJPEG_URL", "SIMANEAT_TEST_HTTP_MJPEG_URLS",
            "SIMANEAT_TEST_HTTP_MJPEG_FPS",
        })

    def test_none_does_not_read_config_or_fetch_catalog(self):
        self.config.unlink()
        result = self.setup("none")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.environment(), {"EXISTING": "preserved"})

    def test_invalid_required_groups_never_write_partial_environment(self):
        first_http = self.catalog["links"]["mjpeg-http"][0]
        for problem in ("missing group", "invalid URL", "fractional FPS"):
            with self.subTest(problem=problem):
                self.catalog["links"]["mjpeg-http"] = {
                    "missing group": [], "invalid URL": [first_http + "\nOTHER=value"],
                    "fractional FPS": [first_http],
                }[problem]
                for source in self.catalog["sources"]:
                    if source["url"] == first_http:
                        source["fps"] = 29.97
                result = self.setup("all")
                self.assertEqual(result.returncode, 1)
                self.assertIn("mjpeg-http", result.stderr)
                self.assertEqual(self.environment(), {"EXISTING": "preserved"})


if __name__ == "__main__":
    unittest.main()
