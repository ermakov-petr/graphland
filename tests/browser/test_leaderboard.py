"""Actual Chromium interaction tests over a local, deterministic static-site fixture.

Run with an environment containing playwright and `playwright install chromium`.
These tests never use or alter the user's browser or published site.
"""
import copy
import functools
import importlib.util
import json
import re
import shutil
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAYWRIGHT_AVAILABLE = importlib.util.find_spec('playwright') is not None


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, 'Install Playwright and Chromium to run browser acceptance tests.')
class LeaderboardBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        site = cls.root / 'graphland'
        shutil.copytree(ROOT / 'site', site)
        (site / 'data').mkdir()
        cls.payload = {
            'config': json.loads((ROOT / 'leaderboard/config.json').read_text()),
            'datasets': json.loads((ROOT / 'leaderboard/datasets.json').read_text())['datasets'],
            'submissions': [json.loads(path.read_text()) for path in sorted((ROOT / 'tests/leaderboard/fixtures/demo_submissions').glob('*.json'))],
        }
        (site / 'data/leaderboard.json').write_text(json.dumps(cls.payload))
        (site / 'leaderboard.csv').write_text('submission_id,value\ndemo-atlas,0.8\n')
        (site / 'leaderboard.xlsx').write_bytes(b'fixture-download')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=cls.temp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}/graphland/'
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

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 1280, 'height': 900})
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def open(self, query=''):
        self.page.goto(self.base + query)
        self.page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'false'")
        self.assertFalse(self.page.locator('#load-error').is_visible())

    def with_payload(self, payload):
        self.page.route('**/data/leaderboard.json', lambda route: route.fulfill(json=payload))

    def test_sort_keeps_keyboard_focus_and_numeric_order(self):
        self.open()
        header = self.page.locator('[data-sort-key="hm-categories"]')
        header.focus()
        header.press('Space')
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.sortKey'), 'hm-categories')
        self.assertEqual(header.locator('..').get_attribute('aria-sort'), 'descending')
        names = self.page.locator('#leaderboard-body .model-button strong').all_text_contents()
        expected = sorted(self.payload['submissions'], key=lambda item: (
            -next((r['value'] for r in item['results'] if r['dataset'] == 'hm-categories' and r['setting'] == 'RL'), -float('inf')),
            item['model_name']))
        self.assertEqual(names, [item['model_name'] for item in expected])
        header.press('Enter')
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.sortKey'), 'hm-categories')
        self.assertEqual(header.locator('..').get_attribute('aria-sort'), 'ascending')

    def test_tabs_filters_dialog_deep_link_and_history(self):
        self.open('?setting=RH&task=multiclass_node_classification&q=Atlas&code=1&sort=hm-categories&order=desc&submission=demo-atlas')
        self.assertEqual(self.page.locator('#model-search').input_value(), 'Atlas')
        self.assertTrue(self.page.locator('#code-filter').is_checked())
        self.assertTrue(self.page.locator('#model-dialog').is_visible())
        self.assertIn('Synthetic demo', self.page.locator('#dialog-content').inner_text())
        self.assertNotIn('Reproduced', self.page.locator('.dialog-badges').inner_text())
        self.page.keyboard.press('Escape')
        self.assertFalse(self.page.locator('#model-dialog').is_visible())
        self.page.go_back()
        self.assertTrue(self.page.locator('#model-dialog').is_visible())
        self.page.go_forward()
        self.assertFalse(self.page.locator('#model-dialog').is_visible())
        self.page.locator('#setting-tab-RH').focus()
        self.page.keyboard.press('ArrowRight')
        self.assertEqual(self.page.locator('#setting-tab-TH').get_attribute('aria-selected'), 'true')
        self.page.go_back()
        self.assertEqual(self.page.locator('#setting-tab-RH').get_attribute('aria-selected'), 'true')
        self.assertEqual(self.page.locator('#model-search').input_value(), 'Atlas')

    def test_model_dialog_returns_focus_to_trigger(self):
        self.open()
        button = self.page.locator('.model-button').first
        button.focus()
        button.press('Enter')
        self.page.keyboard.press('Escape')
        self.assertEqual(self.page.evaluate('document.activeElement.className'), 'model-button')

    def test_history_focus_falls_back_when_previous_dataset_sort_becomes_unavailable(self):
        self.open('?setting=RL&task=binary_node_classification')
        self.page.locator('[data-sort-key="city-reviews"]').click()
        self.page.locator('#setting-tab-TH').click()
        self.page.go_back()
        self.page.locator('[data-sort-key="city-reviews"]').focus()
        self.page.go_forward()
        self.assertTrue(self.page.locator('[data-sort-key="city-reviews"]').is_disabled())
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.sortKey'), 'model')

    def test_mobile_menu_contains_focus_and_unlocks_at_768(self):
        self.page.set_viewport_size({'width': 740, 'height': 800})
        self.open()
        self.page.locator('.menu-button').click()
        self.assertTrue(self.page.locator('#main-content').evaluate('(element) => element.inert'))
        last = self.page.locator('#primary-navigation a').last
        last.focus()
        self.page.keyboard.press('Tab')
        self.assertEqual(self.page.evaluate('document.activeElement.className'), 'menu-button')
        self.page.set_viewport_size({'width': 800, 'height': 800})
        self.page.wait_for_function("!document.body.classList.contains('nav-open')")
        self.assertFalse(self.page.locator('#main-content').evaluate('(element) => element.inert'))
        self.assertEqual(self.page.locator('.menu-button').get_attribute('aria-expanded'), 'false')

    def test_three_empty_states_and_filter_focus(self):
        payload = copy.deepcopy(self.payload)
        payload['submissions'] = []
        self.with_payload(payload)
        self.open()
        self.assertEqual(self.page.locator('#empty-state h3').inner_text(), 'No results yet')
        self.page.unroute('**/data/leaderboard.json')
        payload['submissions'] = [copy.deepcopy(self.payload['submissions'][0])]
        payload['submissions'][0]['results'] = [{'setting': 'RL', 'dataset': 'city-reviews', 'value': .7}]
        self.with_payload(payload)
        self.open('?task=node_regression')
        self.assertIn('No results for this setting', self.page.locator('#empty-state h3').inner_text())
        self.page.unroute('**/data/leaderboard.json')
        self.open()
        self.page.locator('#model-search').fill('no-such-model')
        self.assertEqual(self.page.locator('#empty-state h3').inner_text(), 'No models match these filters')
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'model-search')

    def test_missing_unavailable_and_negative_r2_are_distinct(self):
        self.open('?setting=TH&task=node_regression')
        self.assertTrue(self.page.locator('[data-state="unavailable"]').count() > 0)
        self.assertTrue(self.page.locator('[data-state="missing"]').count() > 0)
        self.assertTrue(any(text.startswith('-') for text in self.page.locator('[data-state="value"]').all_text_contents()))
        self.assertNotIn('Reproduced', self.page.locator('#leaderboard-body').inner_text())

    def test_matching_model_with_no_current_view_results_has_specific_empty_state(self):
        payload = copy.deepcopy(self.payload)
        selected = payload['submissions'][0]
        selected['results'] = [{'setting': 'RL', 'dataset': 'city-reviews', 'value': .7}]
        self.with_payload(payload)
        self.open('?task=node_regression')
        self.assertTrue(self.page.locator('#leaderboard-body tr').count() > 0)
        self.page.locator('#model-search').fill(selected['model_name'])
        self.assertEqual(self.page.locator('#empty-state h3').inner_text(),
                         'Matching models have no results in this view')
        self.assertIn('setting or task', self.page.locator('#empty-state p').inner_text())

    def test_failed_load_retry_and_download_fallback(self):
        requests = []
        def fail_once(route):
            requests.append(True)
            if len(requests) == 1: route.fulfill(status=503, body='unavailable')
            else: route.fulfill(json=self.payload)
        self.page.route('**/data/leaderboard.json', fail_once)
        self.page.goto(self.base)
        self.page.wait_for_selector('#load-error', state='visible')
        self.assertEqual(self.page.locator('#load-error a[href="leaderboard.xlsx"]').count(), 1)
        self.assertEqual(self.page.locator('#load-error a[href="leaderboard.csv"]').count(), 1)
        self.page.locator('#retry-load').click()
        self.page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'false'")
        self.assertFalse(self.page.locator('#load-error').is_visible())
        self.assertTrue(self.page.locator('#leaderboard-body tr').count() > 0)
        self.assertEqual(len(requests), 2)

    def test_no_page_overflow_on_common_viewports(self):
        for width in (320, 375, 768, 1280):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open()
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))

    def test_footer_link_text_contrast_normal_and_hover_desktop_and_mobile(self):
        def channels(css_color):
            values = [float(value) for value in re.findall(r'[\d.]+', css_color)]
            self.assertIn(len(values), (3, 4), css_color)
            self.assertTrue(len(values) == 3 or values[3] == 1, css_color)
            return values[:3]

        def luminance(rgb):
            values = [value / 255 for value in rgb]
            linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                      for value in values]
            return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))

        self.footer_contrast_observations = []
        for width in (1280, 375):
            self.page.set_viewport_size({'width': width, 'height': 900})
            self.open()
            links = self.page.locator('.footer-links a')
            for index in range(links.count()):
                link = links.nth(index)
                for state in ('normal', 'hover'):
                    with self.subTest(width=width, link=index, state=state):
                        if state == 'hover':
                            link.hover()
                        else:
                            self.page.mouse.move(0, 0)
                        measurement = link.evaluate('''(element) => {
                            const foreground = getComputedStyle(element);
                            let ancestor = element;
                            while (ancestor) {
                                const background = getComputedStyle(ancestor).backgroundColor;
                                const values = background.match(/[\\d.]+/g).map(Number);
                                const alpha = values.length === 4 ? values[3] : 1;
                                if (alpha === 1) return {
                                    foreground: foreground.color, background,
                                    font_size: foreground.fontSize, opacity: foreground.opacity,
                                    hovered: element.matches(':hover'), text: element.textContent.trim()
                                };
                                if (alpha !== 0) throw new Error('Unexpected translucent footer background');
                                ancestor = ancestor.parentElement;
                            }
                            throw new Error('No opaque footer background');
                        }''')
                        self.assertEqual(measurement['opacity'], '1')
                        self.assertEqual(measurement['hovered'], state == 'hover')
                        foreground = luminance(channels(measurement['foreground']))
                        background = luminance(channels(measurement['background']))
                        ratio = (max(foreground, background) + .05) / (min(foreground, background) + .05)
                        measurement.update(width=width, state=state, ratio=ratio)
                        self.footer_contrast_observations.append(measurement)
                        self.assertGreaterEqual(ratio, 4.5, measurement)

    def test_mobile_initial_view_shows_model_and_first_numeric_score(self):
        self.page.set_viewport_size({'width': 375, 'height': 900})
        self.open()
        row = self.page.locator('#leaderboard-body tr').first
        model = row.locator('td').nth(0).bounding_box()
        score = row.locator('td').nth(1).bounding_box()
        container = self.page.locator('.table-scroll').bounding_box()
        self.assertLessEqual(model['width'], 180)
        self.assertGreater(score['x'], model['x'])
        self.assertLessEqual(score['x'] + score['width'], container['x'] + container['width'] + 1)
        self.assertEqual(row.locator('td').nth(1).get_attribute('data-state'), 'value')


if __name__ == '__main__': unittest.main()
