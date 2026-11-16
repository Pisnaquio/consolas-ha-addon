from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from server.app import Handler, Server, init_db


class TestConfig:
    def __init__(self, root: Path) -> None:
        self.data_dir = root / "data"
        self.static_dir = root / "web"
        self.media_dir = self.data_dir / "media"
        self.auction_watch_dir = self.data_dir / "auction-watch"
        self.db_path = self.data_dir / "consolas.sqlite"
        self.max_body_size = 1024 * 1024


class UnknownApiRouteTests(unittest.TestCase):
    """Una ruta de API que no existe responde 404 en JSON, no la página de inicio."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = TestConfig(Path(self.tmp.name))
        self.config.static_dir.mkdir(parents=True)
        (self.config.static_dir / "index.html").write_text("<!doctype html><title>inicio</title>")
        (self.config.static_dir / "radar.html").write_text("<!doctype html><title>radar</title>")
        init_db(self.config)
        self.server = Server(("127.0.0.1", 0), Handler, self.config)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def get(self, path: str) -> tuple[int, str, str]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, response.headers.get("Content-Type", ""), response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Content-Type", ""), error.read().decode()

    def test_an_unknown_api_route_is_a_json_404(self) -> None:
        for path in ("/api/no-existe", "/api", "/api/radar/no-existe", "/api/no-existe?x=1"):
            with self.subTest(path=path):
                status, content_type, body = self.get(path)
                self.assertEqual(status, 404)
                self.assertIn("application/json", content_type)
                self.assertEqual(json.loads(body), {"ok": False, "error": "Unknown endpoint"})

    def test_pages_keep_their_static_fallback(self) -> None:
        status, content_type, body = self.get("/radar.html")
        self.assertEqual(status, 200)
        self.assertIn("radar", body)
        # Una página desconocida sigue cayendo en el inicio: sólo cambia /api.
        status, content_type, body = self.get("/no-existe.html")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("inicio", body)

    def test_known_api_routes_still_answer(self) -> None:
        status, content_type, body = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])


if __name__ == "__main__":
    unittest.main()
