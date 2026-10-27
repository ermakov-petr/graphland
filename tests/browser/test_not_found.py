"""Check the built 404 document at missing URLs, with GitHub Pages semantics."""
import functools
import importlib.util
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class MissingPageHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        if code != 404:
            return super().send_error(code, message, explain)
        body = (Path(self.directory) / "graphland/404.html").read_bytes()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Install Playwright and Chromium.")
class NotFoundBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.temp = tempfile.TemporaryDirectory()
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/leaderboard/build.py"),
             "--output", str(Path(cls.temp.name) / "graphland")],
            check=True, capture_output=True, text=True,
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(MissingPageHandler, directory=cls.temp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/graphland/"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def test_nested_missing_urls_keep_assets_and_recovery_links_without_javascript(self):
        for javascript in (True, False):
            for path in ("missing.html", "missing/deep/page", "missing/deep/"):
                with self.subTest(javascript=javascript, path=path):
                    page = self.browser.new_page(java_script_enabled=javascript, viewport={"width": 320, "height": 800})
                    failures = []
                    page.on("response", lambda r: failures.append(r.url) if r.request.resource_type != "document" and r.status >= 400 else None)
                    response = page.goto(self.base + path + "?q=unknown#unused")
                    self.assertEqual(response.status, 404)
                    self.assertEqual(page.title(), "Page not found · GraphLand")
                    self.assertEqual(page.locator("h1").inner_text(), "Page not found")
                    self.assertEqual(page.locator(".project-navigation a").count(), 4)
                    self.assertEqual(page.locator('[aria-current="page"]').count(), 0)
                    self.assertEqual(page.locator(".site-header a").get_attribute("href"), "https://research.yandex.com/")
                    self.assertTrue(page.locator(".wordmark img").evaluate_all("els => els.every(e => e.complete && e.naturalWidth > 0)"))
                    self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                    self.assertIn("YSText", page.locator("h1").evaluate("e => getComputedStyle(e).fontFamily"))
                    self.assertEqual(failures, [])
                    page.get_by_role("link", name="Go to homepage", exact=True).click()
                    self.assertEqual(page.url, self.base + "index.html")
                    self.assertEqual(page.locator("h1").inner_text(), "GraphLand")
                    page.close()

    def test_skip_link_stays_on_the_missing_document(self):
        page = self.browser.new_page()
        page.goto(self.base + "unknown/deep/page")
        page.keyboard.press("Tab")
        self.assertEqual(page.locator(":focus").inner_text(), "Skip to content")
        page.keyboard.press("Enter")
        page.wait_for_function("document.activeElement.id === 'main-content'")
        self.assertEqual(page.url, self.base + "unknown/deep/page#main-content")
        self.assertEqual(page.locator(":focus").get_attribute("id"), "main-content")
        self.assertEqual(page.title(), "Page not found · GraphLand")
        page.close()
