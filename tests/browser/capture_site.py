"""Render an already built local production artifact; no publish/user-browser access."""
import argparse
import functools
import json
import shutil
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--site-dir', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        shutil.copytree(args.site_dir, Path(directory) / 'graphland')
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=directory))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1280, 'height': 900}, device_scale_factor=1)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                base = f'http://127.0.0.1:{server.server_port}/graphland/'
                page.goto(base)
                page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'false'")
                if page.locator('#load-error').is_visible(): raise RuntimeError('Production artifact failed to load.')
                page.screenshot(path=str(args.output_dir / 'desktop-full.png'), full_page=True)
                page.locator('#leaderboard').screenshot(path=str(args.output_dir / 'desktop-table.png'))
                page.locator('.model-button').first.click()
                page.locator('#model-dialog').screenshot(path=str(args.output_dir / 'desktop-dialog.png'))
                page.keyboard.press('Escape')
                page.set_viewport_size({'width': 375, 'height': 900})
                page.screenshot(path=str(args.output_dir / 'mobile-full.png'), full_page=True)
                page.locator('#leaderboard').screenshot(path=str(args.output_dir / 'mobile-table.png'))
                page.locator('.model-button').first.click()
                page.locator('#model-dialog').screenshot(path=str(args.output_dir / 'mobile-dialog.png'))
                report = {'source_directory': str(args.site_dir), 'page_errors': errors,
                          'rows': page.locator('#leaderboard-body tr').count(),
                          'demo_notice_visible': page.locator('#demo-data-notice').is_visible(),
                          'screenshots': sorted(path.name for path in args.output_dir.glob('*.png'))}
                (args.output_dir / 'visual-runtime.json').write_text(json.dumps(report, indent=2))
                browser.close()
                if errors: raise RuntimeError(f'Unexpected page errors: {errors}')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__': main()
