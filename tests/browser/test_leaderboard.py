"""Actual Chromium interaction tests over a local, deterministic static-site fixture.

Run with an environment containing playwright and `playwright install chromium`.
These tests never use or alter the user's browser or published site.
"""
import copy
import functools
import hashlib
import importlib.util
import json
import re
import shutil
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAYWRIGHT_AVAILABLE = importlib.util.find_spec('playwright') is not None

# Official promo-yandex-research/11 assets captured on 2026-10-08.
# Font bytes come from the official CSS/WOFF2; SVG geometry was independently
# compared with About header and Publications hero source, excluding packaging.
REFERENCE_FONT_SHA256 = {
    'ys-text-regular.woff2': 'de4fb43ce43b6134c3e063b137f3933c046f2d4829a8687127c6e49fa6248ecd',
    'ys-text-medium.woff2': 'f0aa37cda27c0a4cba5fa7dffe585cd358235ddf052afc950d7aa35f73d7b3f1',
    'ys-text-bold.woff2': '716caf675db710027ba82e1a6b4d0061b65b7e7bef28db3c669384d3c2fb7e88',
    'ys-text-light.woff2': 'e934364fca67fd714de1daf3b0f802da85500d52acbcc9489cead25ccbd98988',
    'merriweather-light.woff2': '1fcd5c0eeff57dc1cc85d599f7c414aedccd6149d16cc091e78c8ae376a28539',
}
REFERENCE_SVG_GEOMETRY_SHA256 = {
    'yandex.svg': 'b8d7cc2e59235d4b5f158a7b500215389abcdb6a4194e7273803f4b996b81207',
    'research.svg': 'ca9f57dca620f6c7d65fc11f3a46ea47f42125fcc8db2fefff2e868d59efb613',
    'research-circles.svg': '25bd12f234db75ddf411abcc4e923478ebdd828b614305c793f3f093f39671e6',
}


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
        cls.site_source_sha256 = {name: hashlib.sha256((site / name).read_bytes()).hexdigest()
                                  for name in ('index.html', 'assets/styles.css', 'assets/app.js')}
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
        cls.brand_observations = []

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
        self.page.evaluate('async () => { await document.fonts.ready; }')
        self.assertFalse(self.page.locator('#load-error').is_visible())

    def with_payload(self, payload):
        self.page.route('**/data/leaderboard.json', lambda route: route.fulfill(json=payload))

    def actual_platform_fonts(self, selectors):
        cdp = self.page.context.new_cdp_session(self.page)
        try:
            cdp.send('DOM.enable')
            cdp.send('CSS.enable')
            root = cdp.send('DOM.getDocument', {'depth': 1})['root']['nodeId']
            results = {}
            for selector in selectors:
                node = cdp.send('DOM.querySelector', {'nodeId': root, 'selector': selector})['nodeId']
                self.assertNotEqual(node, 0, selector)
                results[selector] = cdp.send('CSS.getPlatformFontsForNode', {'nodeId': node})['fonts']
            return results
        finally:
            cdp.detach()

    def assert_custom_glyph_font(self, fonts, postscript_name):
        rendered = [font for font in fonts if font['glyphCount'] > 0]
        self.assertTrue(rendered, f'No rendered glyphs: {fonts}')
        self.assertTrue(all(font['isCustomFont'] and font['postScriptName'] == postscript_name
                            for font in rendered), f'Expected custom {postscript_name}; rendered {rendered}')

    def test_brand_fonts_are_loaded_and_actually_render_glyphs(self):
        responses = {}
        self.page.on('response', lambda response: responses.update({response.url.rsplit('/', 1)[-1]: response.status})
                     if response.request.resource_type == 'font' else None)
        self.open()
        for name in ('ys-text-regular.woff2', 'ys-text-medium.woff2', 'merriweather-light.woff2'):
            self.assertEqual(responses.get(name), 200, responses)
        expected = {'h1': 'YSText-Medium', '#primary-navigation a': 'YSText-Regular',
                    '.hero-intro': 'Merriweather-Light'}
        fonts = self.actual_platform_fonts(expected)
        for selector, postscript_name in expected.items():
            self.assert_custom_glyph_font(fonts[selector], postscript_name)
        self.brand_observations.append({'check': 'actual_fonts', 'responses': responses, 'platform_fonts': fonts})

    def test_brand_font_404_is_detected_despite_css_family_remaining_set(self):
        self.page.route('**/assets/brand/merriweather-light.woff2',
                        lambda route: route.fulfill(status=404, body='missing-font'))
        self.open()
        declared = self.page.locator('.hero-intro').evaluate('(el) => getComputedStyle(el).fontFamily')
        self.assertIn('Merriweather', declared)
        fonts = self.actual_platform_fonts(['.hero-intro'])['.hero-intro']
        with self.assertRaisesRegex(AssertionError, 'Expected custom Merriweather-Light'):
            self.assert_custom_glyph_font(fonts, 'Merriweather-Light')
        faces = self.page.evaluate("() => [...document.fonts].filter(f => f.family === 'Merriweather').map(f => f.status)")
        self.assertEqual(faces, ['error'])
        self.brand_observations.append({'check': 'font_404_detection', 'declared': declared,
                                        'platform_fonts': fonts, 'face_status': faces})

    def test_brand_assets_serve_official_font_bytes_and_svg_geometry(self):
        self.open()
        measured = {}
        for name, expected in REFERENCE_FONT_SHA256.items():
            response = self.page.request.get(self.base + 'assets/brand/' + name)
            self.assertEqual(response.status, 200, name)
            data = response.body()
            self.assertEqual(data[:4], b'wOF2', name)
            actual = hashlib.sha256(data).hexdigest()
            self.assertEqual(actual, expected, name)
            measured[name] = {'status': response.status, 'sha256': actual, 'bytes': len(data)}
        geometry_keys = {'d', 'cx', 'cy', 'r', 'x', 'y', 'width', 'height', 'x1', 'y1',
                         'x2', 'y2', 'points', 'transform', 'viewBox'}
        for name, expected in REFERENCE_SVG_GEOMETRY_SHA256.items():
            response = self.page.request.get(self.base + 'assets/brand/' + name)
            self.assertEqual(response.status, 200, name)
            root = ET.fromstring(response.body())
            geometry = [(node.tag.rsplit('}', 1)[-1], sorted((key, value) for key, value in node.attrib.items()
                         if key in geometry_keys)) for node in root.iter()]
            actual = hashlib.sha256(json.dumps(geometry, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(actual, expected, name)
            measured[name] = {'status': response.status, 'geometry_sha256': actual}
        images = self.page.locator('.wordmark img, .hero-art img').evaluate_all(
            '''(nodes) => nodes.map(el => ({src:el.getAttribute("src"),complete:el.complete,
              naturalWidth:el.naturalWidth,naturalHeight:el.naturalHeight}))''')
        self.assertEqual(len(images), 3)
        self.assertTrue(all(item['complete'] and item['naturalWidth'] > 0 and item['naturalHeight'] > 0 for item in images), images)
        self.assertEqual({item['src'].rsplit('/', 1)[-1] for item in images}, set(REFERENCE_SVG_GEOMETRY_SHA256))
        self.brand_observations.append({'check': 'served_asset_identity', 'assets': measured, 'rendered_images': images})

    def test_brand_header_container_and_svg_lockup_match_reference_boundaries(self):
        self.open()
        for width, shell_width, padding, header_height, logo_parts in (
                (1601, 1420, 30, 63, (83, 99, 31)), (1600, 968, 20, 49, (59, 70, 22)),
                (1000, 968, 20, 49, (59, 70, 22)), (999, 736, 20, 49, (59, 70, 22)),
                (768, 736, 20, 49, (59, 70, 22)), (767, 767, 16, 53, (75, 89, 28)),
                (375, 375, 16, 53, (75, 89, 28)), (320, 320, 16, 53, (75, 89, 28))):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.page.evaluate('async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); }')
                header = self.page.locator('.site-header').bounding_box()
                shell = self.page.locator('.header-inner').bounding_box()
                logo = self.page.locator('.wordmark').bounding_box()
                parts = [self.page.locator('.wordmark img').nth(i).bounding_box() for i in range(2)]
                self.assertEqual(self.page.locator('.site-header').evaluate('(el) => getComputedStyle(el).position'), 'fixed')
                self.assertAlmostEqual(header['y'], 0, delta=.5)
                self.assertAlmostEqual(header['width'], width, delta=.5)
                self.assertAlmostEqual(header['height'], header_height, delta=.5)
                self.assertAlmostEqual(shell['width'], shell_width, delta=.5)
                self.assertAlmostEqual(shell['x'], (width - shell_width) / 2, delta=.5)
                self.assertAlmostEqual(logo['x'], shell['x'] + padding, delta=.5)
                self.assertAlmostEqual(logo['width'], sum(logo_parts[:2]), delta=.5)
                self.assertAlmostEqual(logo['height'], logo_parts[2], delta=.5)
                for part, expected_width in zip(parts, logo_parts[:2]):
                    self.assertAlmostEqual(part['width'], expected_width, delta=.5)
                    self.assertAlmostEqual(part['height'], logo_parts[2], delta=.5)
                self.assertAlmostEqual(parts[1]['x'], parts[0]['x'] + parts[0]['width'], delta=.5)
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
                self.brand_observations.append({'check': 'reference_geometry', 'width': width, 'header': header,
                                                'shell': shell, 'lockup': logo, 'svg_parts': parts})

    def test_header_search_anchor_focuses_visible_native_search_desktop_and_mobile(self):
        for width in (1280, 375):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open()
                if width == 375:
                    self.page.locator('.menu-button').click()
                    self.assertTrue(self.page.locator('#main-content').evaluate('(el) => el.inert'))
                self.page.locator('.header-search').click()
                self.page.wait_for_function("() => document.activeElement.id === 'model-search'")
                self.page.wait_for_function('''() => {
                    const input=document.getElementById('model-search').getBoundingClientRect();
                    const header=document.querySelector('.site-header').getBoundingClientRect();
                    return input.top >= header.bottom && input.bottom <= innerHeight;
                }''')
                self.assertTrue(self.page.url.endswith('#model-search'), self.page.url)
                self.assertFalse(self.page.locator('#main-content').evaluate('(el) => el.inert'))
                self.assertFalse(self.page.locator('.site-header').evaluate('(el) => el.classList.contains("nav-open")'))
                self.brand_observations.append({'check': 'native_header_search', 'width': width,
                    'focused_id': self.page.evaluate('document.activeElement.id'),
                    'search_bounds': self.page.locator('#model-search').bounding_box()})

    def test_keyboard_skip_link_focuses_main_and_continues_inside_content(self):
        for width in (1280, 375):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open()
                self.page.keyboard.press('Tab')
                self.assertEqual(self.page.evaluate('document.activeElement.className'), 'skip-link')
                self.page.keyboard.press('Enter')
                observation = {'check': 'keyboard_skip_link', 'width': width,
                               'target_focus': self.page.evaluate('document.activeElement.id')}
                self.brand_observations.append(observation)
                self.assertEqual(observation['target_focus'], 'main-content')
                self.assertTrue(self.page.url.endswith('#main-content'), self.page.url)
                self.page.keyboard.press('Tab')
                self.assertTrue(self.page.locator('.breadcrumb a').evaluate('(el) => el === document.activeElement'))
                self.page.keyboard.press('Tab')
                self.assertEqual(self.page.evaluate('document.activeElement.id'), 'submit-results-link')
                observation['next_content_focus'] = 'breadcrumb Home then submit-results-link'

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
        def close_and_wait(action):
            # Native close is queued after dialog.open becomes false. Await its
            # focus restoration before exercising the next keyboard control.
            self.page.evaluate('''() => {
                window.__graphlandDialogClosed = false;
                document.getElementById('model-dialog').addEventListener('close',
                    () => { window.__graphlandDialogClosed = true; }, {once:true});
            }''')
            action()
            self.page.wait_for_function('() => window.__graphlandDialogClosed', timeout=5000)

        self.open('?setting=RH&task=multiclass_node_classification&q=Atlas&code=1&sort=hm-categories&order=desc&submission=demo-atlas')
        self.assertEqual(self.page.locator('#model-search').input_value(), 'Atlas')
        self.assertTrue(self.page.locator('#code-filter').is_checked())
        self.assertTrue(self.page.locator('#model-dialog').is_visible())
        self.assertIn('Synthetic demo', self.page.locator('#dialog-content').inner_text())
        self.assertNotIn('Reproduced', self.page.locator('.dialog-badges').inner_text())
        close_and_wait(lambda: self.page.keyboard.press('Escape'))
        self.assertFalse(self.page.locator('#model-dialog').is_visible())
        self.page.go_back()
        self.assertTrue(self.page.locator('#model-dialog').is_visible())
        close_and_wait(self.page.go_forward)
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

    def test_delayed_dialog_close_preserves_focus_already_moved_to_an_outside_control(self):
        self.open('?setting=RH&submission=demo-atlas')
        self.page.evaluate('''() => {
            const dialog=document.getElementById('model-dialog');
            window.__nativeCloseComplete=false;
            // Reproduce the observed focus race deterministically: an outside
            // control acquires focus before the app's queued close handler.
            dialog.addEventListener('close', () => document.getElementById('setting-tab-RH').focus(),
                                    {capture:true,once:true});
            dialog.addEventListener('close', () => {window.__nativeCloseComplete=true;}, {once:true});
        }''')
        self.page.keyboard.press('Escape')
        self.page.wait_for_function('() => window.__nativeCloseComplete', timeout=5000)
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'setting-tab-RH')
        self.page.keyboard.press('ArrowRight')
        self.assertEqual(self.page.locator('#setting-tab-TH').get_attribute('aria-selected'), 'true')
        self.brand_observations.append({'check': 'native_close_preserves_outside_focus',
                                        'focused_id': self.page.evaluate('document.activeElement.id')})

    def test_stale_close_event_does_not_clear_reopened_dialog_trigger(self):
        self.open('?submission=demo-atlas')
        self.page.evaluate('''() => {
            const dialog=document.getElementById('model-dialog');
            window.__staleCloseComplete=false;
            window.__reopenTrigger=document.querySelectorAll('.model-button')[1];
            dialog.addEventListener('close', () => {
                window.__beforeReopenFocus=document.activeElement.tagName;
                window.__reopenTrigger.click();
            }, {capture:true,once:true});
            dialog.addEventListener('close', () => {window.__staleCloseComplete=true;}, {once:true});
        }''')
        self.page.keyboard.press('Escape')
        self.page.wait_for_function('() => window.__staleCloseComplete', timeout=5000)
        self.assertTrue(self.page.locator('#model-dialog').is_visible())
        expected_name = self.page.evaluate("window.__reopenTrigger.querySelector('strong').textContent")
        self.assertIn(expected_name, self.page.locator('#dialog-title').inner_text())
        self.page.evaluate('''() => {
            window.__reopenedCloseComplete=false;
            document.getElementById('model-dialog').addEventListener('close',
                () => {window.__reopenedCloseComplete=true;}, {once:true});
        }''')
        self.page.locator('[data-dialog-close]').click()
        self.page.wait_for_function('() => window.__reopenedCloseComplete', timeout=5000)
        observation = self.page.evaluate('''() => ({check:'stale_close_keeps_reopened_trigger',
            before_reopen_focus:window.__beforeReopenFocus,focused_id:document.activeElement.id,
            expected_model:window.__reopenTrigger.querySelector('strong').textContent,
            focused_expected_trigger:document.activeElement===window.__reopenTrigger})''')
        self.brand_observations.append(observation)
        self.assertTrue(observation['focused_expected_trigger'], observation)

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
        self.page.set_viewport_size({'width': 768, 'height': 800})
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
        for width in (320, 375):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open()
                row = self.page.locator('#leaderboard-body tr').first
                model = row.locator('td').nth(0).bounding_box()
                cell = row.locator('td').nth(1)
                score = cell.bounding_box()
                metric = cell.evaluate('''(el) => {
                    const range=document.createRange();
                    range.selectNodeContents(el);
                    return range.getBoundingClientRect().toJSON();
                }''')
                container = self.page.locator('.table-scroll').bounding_box()
                self.assertLessEqual(model['width'], 180)
                self.assertGreater(score['x'], model['x'])
                self.assertLessEqual(score['x'] + score['width'], container['x'] + container['width'] + 1)
                self.assertEqual(cell.get_attribute('data-state'), 'value')
                self.assertGreater(metric['width'], 0)
                self.assertGreaterEqual(metric['left'], max(container['x'], score['x']))
                self.assertLessEqual(metric['right'], min(container['x'] + container['width'], score['x'] + score['width']) + 1)
                self.brand_observations.append({'check': 'mobile_first_metric_visible', 'width': width,
                    'model': model, 'score_cell': score, 'metric_text': metric, 'container': container})


if __name__ == '__main__': unittest.main()
