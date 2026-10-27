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
                                  for name in ('index.html', 'datasets.html', 'leaderboard.html', 'getting-started.html',
                                               'assets/styles.css', 'assets/app.js', 'assets/navigation.js',
                                               'assets/leaderboard-metadata.css')}
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
        self.page.goto(self.base + 'leaderboard.html' + query)
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
        expected = {'h1': 'YSText-Medium', '.project-navigation a[href="index.html"]': 'YSText-Regular',
                    '.page-lead': 'Merriweather-Light'}
        fonts = self.actual_platform_fonts(expected)
        for selector, postscript_name in expected.items():
            self.assert_custom_glyph_font(fonts[selector], postscript_name)
        self.brand_observations.append({'check': 'actual_fonts', 'responses': responses, 'platform_fonts': fonts})

    def test_brand_font_404_is_detected_despite_css_family_remaining_set(self):
        self.page.route('**/assets/brand/merriweather-light.woff2',
                        lambda route: route.fulfill(status=404, body='missing-font'))
        self.open()
        declared = self.page.locator('.page-lead').evaluate('(el) => getComputedStyle(el).fontFamily')
        self.assertIn('Merriweather', declared)
        fonts = self.actual_platform_fonts(['.page-lead'])['.page-lead']
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
        images = self.page.locator('.wordmark img').evaluate_all(
            '''(nodes) => nodes.map(el => ({src:el.getAttribute("src"),complete:el.complete,
              naturalWidth:el.naturalWidth,naturalHeight:el.naturalHeight}))''')
        self.assertEqual(len(images), 2)
        self.assertTrue(all(item['complete'] and item['naturalWidth'] > 0 and item['naturalHeight'] > 0 for item in images), images)
        self.assertEqual({item['src'].rsplit('/', 1)[-1] for item in images}, {'yandex.svg', 'research.svg'})
        self.assertEqual(self.page.locator('.hero-art').count(), 0)
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

    def test_header_contains_only_logo_and_navigates_to_its_external_destination(self):
        destination = 'https://research.yandex.com/'
        requested_urls = []

        def official_destination_fixture(route):
            requested_urls.append(route.request.url)
            route.fulfill(status=200, content_type='text/html; charset=utf-8',
                          body='<!doctype html><title>Destination fixture</title><main>External logo destination</main>')

        self.page.route(destination, official_destination_fixture)
        for width in (1280, 375):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open()
                header = self.page.locator('.site-header')
                logo = header.locator('.wordmark')
                self.assertEqual(header.locator('a').count(), 1)
                self.assertEqual(header.locator('button, nav, input, select, textarea').count(), 0)
                self.assertEqual(header.locator('.header-inner > *').count(), 1)
                self.assertEqual(header.inner_text().strip(), '')
                self.assertEqual(logo.get_attribute('href'), destination)
                self.assertIn('Yandex Research', logo.get_attribute('aria-label'))
                self.assertIsNone(logo.get_attribute('target'))
                self.assertTrue(logo.is_visible())
                previous_requests = len(requested_urls)
                logo.click()
                self.page.wait_for_url(destination)
                self.assertEqual(self.page.url, destination)
                self.assertEqual(self.page.locator('main').inner_text(), 'External logo destination')
                self.assertEqual(requested_urls[previous_requests:], [destination])
                self.brand_observations.append({'check': 'logo_only_header_external_navigation',
                    'width': width, 'destination': self.page.url, 'header_links': 1,
                    'navigation_fixture': True})

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
                self.assertEqual(self.page.evaluate('document.activeElement.id'), 'submit-results-link')
                observation['next_content_focus'] = 'submit-results-link'

    def test_code_filter_feedback_explains_unchanged_rows_and_filters_mixed_code(self):
        payload = copy.deepcopy(self.payload)
        payload['config']['default_filters']['only_models_with_code'] = False
        payload['submissions'] = payload['submissions'][:3]
        for submission in payload['submissions']:
            submission['code_availability'] = 'available'
        self.with_payload(payload)
        self.open()
        names_before = self.page.locator('#leaderboard-body .model-button strong').all_text_contents()
        self.assertEqual(len(names_before), 3)
        status = self.page.locator('#filter-status')
        self.assertEqual(status.count(), 1, 'Applied filters need a dedicated visible status')
        self.assertEqual(status.get_attribute('role'), 'status')
        self.page.locator('label.toggle').click()
        self.assertTrue(self.page.locator('#code-filter').is_checked())
        self.assertEqual(self.page.locator('#leaderboard-body .model-button strong').all_text_contents(), names_before)
        self.assertTrue(status.is_visible())
        self.assertRegex(status.inner_text(), r'(?i)code')
        self.assertRegex(status.inner_text(), r'(?i)all\s+3')
        self.brand_observations.append({'check': 'all_code_filter_feedback', 'models_before': names_before,
                                        'models_after': names_before, 'status': status.inner_text()})

        self.page.unroute('**/data/leaderboard.json')
        payload['submissions'][-1]['code_availability'] = 'unavailable'
        self.with_payload(payload)
        self.open()
        self.assertEqual(self.page.locator('#leaderboard-body tr').count(), 3)
        self.page.locator('label.toggle').click()
        expected_names = [submission['model_name'] for submission in payload['submissions']
                          if submission['code_availability'] == 'available']
        actual_names = self.page.locator('#leaderboard-body .model-button strong').all_text_contents()
        self.assertCountEqual(actual_names, expected_names)
        self.assertEqual(len(actual_names), 2)
        mixed_status = status.inner_text()
        self.assertIn('2 of 3 matching models have code', mixed_status)
        self.page.locator('#model-search').fill('Atlas')
        self.assertIn('Atlas', status.inner_text())
        self.assertEqual(self.page.locator('#leaderboard-body tr').count(), 1)
        self.brand_observations.append({'check': 'mixed_code_filter_feedback', 'models_after': actual_names,
                                        'status': mixed_status, 'search_status': status.inner_text()})
        unavailable_name = payload['submissions'][-1]['model_name']
        self.page.locator('#model-search').fill(unavailable_name)
        self.assertEqual(self.page.locator('#leaderboard-body tr').count(), 0)
        self.assertIn(unavailable_name, status.inner_text())
        self.assertIn('Code filter on · the matching model has no available code', status.inner_text())
        self.brand_observations.append({'check': 'code_unavailable_singleton_feedback',
                                        'query': unavailable_name, 'visible_models': 0, 'status': status.inner_text()})

    def test_keyboard_reset_filters_preserves_view_and_explicit_off_history(self):
        payload = copy.deepcopy(self.payload)
        payload['config']['default_filters']['only_models_with_code'] = True
        self.with_payload(payload)
        self.open('?setting=RH&task=binary_node_classification&sort=city-reviews&order=desc&q=Atlas')
        self.assertTrue(self.page.locator('#code-filter').is_checked())
        reset = self.page.locator('#reset-filters')
        self.assertEqual(reset.count(), 1, 'Active filters need an accessible reset control')
        self.assertTrue(reset.is_visible())
        self.page.locator('#code-filter').focus()
        self.page.keyboard.press('Tab')
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'reset-filters')
        self.page.keyboard.press('Enter')
        self.page.wait_for_function("document.activeElement.id === 'model-search'")
        self.assertEqual(self.page.locator('#model-search').input_value(), '')
        self.assertFalse(self.page.locator('#code-filter').is_checked())
        self.assertFalse(reset.is_visible())
        self.assertEqual(self.page.locator('#setting-tab-RH').get_attribute('aria-selected'), 'true')
        self.assertEqual(self.page.locator('#task-tab-binary_node_classification').get_attribute('aria-selected'), 'true')
        self.assertEqual(self.page.locator('[data-sort-key="city-reviews"]').locator('..').get_attribute('aria-sort'), 'descending')
        expected = {'setting': 'RH', 'task': 'binary_node_classification', 'sort': 'city-reviews',
                    'order': 'desc', 'code': '0'}
        query_after_reset = self.page.evaluate('Object.fromEntries(new URL(location.href).searchParams)')
        self.assertEqual(query_after_reset, expected)
        self.page.reload()
        self.page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'false'")
        self.assertFalse(self.page.locator('#code-filter').is_checked(), 'Explicit code=0 must override the true config default')
        self.assertFalse(reset.is_visible())
        self.page.go_back()
        self.page.wait_for_function("document.getElementById('model-search').value === 'Atlas' && document.getElementById('code-filter').checked")
        self.assertTrue(reset.is_visible())
        reset.focus()
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'reset-filters')
        self.page.go_forward()
        self.page.wait_for_function("document.getElementById('model-search').value === '' && !document.getElementById('code-filter').checked")
        self.assertFalse(reset.is_visible())
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'model-search')
        self.assertEqual(self.page.evaluate('Object.fromEntries(new URL(location.href).searchParams)'), expected)
        self.brand_observations.append({'check': 'keyboard_reset_filters', 'reset_query': query_after_reset,
                                        'reload_explicit_off': True, 'back_forward_restored': True,
                                        'history_hides_focused_reset_safely': True})

    def test_scroll_hint_tracks_mobile_edges_resize_task_and_empty_results(self):
        self.page.set_viewport_size({'width': 375, 'height': 900})
        self.open()
        scroll = self.page.locator('.table-scroll')
        hint = self.page.locator('#table-scroll-hint')
        self.assertEqual(hint.count(), 1, 'Overflowing datasets need a visible discovery hint')
        self.assertTrue(scroll.evaluate('(el) => el.scrollWidth > el.clientWidth'))
        self.page.wait_for_function("!document.getElementById('table-scroll-hint').hidden")
        self.assertTrue(hint.is_visible())
        self.assertRegex(hint.inner_text(), r'(?i)scroll.*dataset')
        self.assertIn('table-scroll-hint', (scroll.get_attribute('aria-describedby') or '').split())
        scroll.evaluate('(el) => { el.scrollLeft = el.scrollWidth; }')
        self.page.wait_for_function("document.getElementById('table-scroll-hint').hidden")
        self.assertFalse(hint.is_visible())
        self.assertNotIn('table-scroll-hint', (scroll.get_attribute('aria-describedby') or '').split())
        scroll.evaluate('(el) => { el.scrollLeft = 0; }')
        self.page.wait_for_function("!document.getElementById('table-scroll-hint').hidden")
        self.page.set_viewport_size({'width': 1601, 'height': 900})
        self.page.wait_for_function("document.querySelector('.table-scroll').scrollWidth <= document.querySelector('.table-scroll').clientWidth")
        self.page.wait_for_function("document.getElementById('table-scroll-hint').hidden")
        self.assertFalse(hint.is_visible())
        self.assertNotIn('table-scroll-hint', (scroll.get_attribute('aria-describedby') or '').split())
        self.page.set_viewport_size({'width': 375, 'height': 900})
        self.page.wait_for_function("!document.getElementById('table-scroll-hint').hidden")
        self.page.locator('#task-tab-node_regression').click()
        self.assertTrue(scroll.evaluate('(el) => el.scrollWidth > el.clientWidth'))
        self.assertTrue(hint.is_visible())
        self.page.locator('#model-search').fill('no-such-model')
        self.assertTrue(self.page.locator('#empty-state').is_visible())
        self.assertFalse(scroll.is_visible())
        self.page.wait_for_function("document.getElementById('table-scroll-hint').hidden", timeout=5000)
        self.assertFalse(hint.is_visible())
        self.assertNotIn('table-scroll-hint', (scroll.get_attribute('aria-describedby') or '').split())
        self.page.locator('#model-search').fill('')
        self.page.wait_for_function("!document.getElementById('table-scroll-hint').hidden")
        self.assertTrue(scroll.is_visible())
        self.assertTrue(hint.is_visible())
        self.assertIn('table-scroll-hint', (scroll.get_attribute('aria-describedby') or '').split())
        self.brand_observations.append({'check': 'conditional_table_scroll_hint', 'widths': [375, 1601],
                                        'right_edge_hidden': True, 'fit_hidden': True, 'empty_hidden': True,
                                        'task_change_and_results_restore': True})

    def test_catalog_scroll_and_native_disclosures_work_with_keyboard(self):
        self.page.set_viewport_size({'width': 375, 'height': 900})
        self.page.goto(self.base)
        self.page.evaluate('document.fonts.ready')
        viewport = self.page.locator('.catalog-scroll')
        hint = self.page.locator('#catalog-scroll-hint')
        self.page.wait_for_function("!document.getElementById('catalog-scroll-hint').hidden")
        self.assertIn('catalog-scroll-hint', viewport.get_attribute('aria-describedby'))
        label = self.page.locator('.dataset-catalog tbody th').first
        left = label.bounding_box()['x']
        viewport.focus()
        self.page.keyboard.press('ArrowRight')
        self.page.wait_for_function("document.querySelector('.catalog-scroll').scrollLeft > 0")
        self.assertAlmostEqual(label.bounding_box()['x'], left, delta=1)
        viewport.evaluate('(el) => el.scrollLeft = el.scrollWidth')
        self.page.wait_for_function("document.getElementById('catalog-scroll-hint').hidden")
        self.assertFalse(hint.is_visible())
        self.assertNotIn('catalog-scroll-hint', (viewport.get_attribute('aria-describedby') or '').split())
        viewport.evaluate('(el) => el.scrollLeft = 0')
        self.page.wait_for_function("!document.getElementById('catalog-scroll-hint').hidden")
        self.page.set_viewport_size({'width': 1920, 'height': 900})
        self.page.wait_for_function("document.getElementById('catalog-scroll-hint').hidden")
        self.assertTrue(viewport.evaluate('(el) => el.scrollWidth <= el.clientWidth + 2'))
        for route, selector in (('', '.citation-disclosure'), ('leaderboard.html', '.protocol-disclosure')):
            self.page.goto(self.base + route)
            disclosure = self.page.locator(selector)
            summary = disclosure.locator('summary')
            summary.focus()
            self.page.keyboard.press('Enter')
            self.assertIsNotNone(disclosure.get_attribute('open'))
            self.page.keyboard.press('Space')
            self.assertIsNone(disclosure.get_attribute('open'))
            self.assertEqual(summary.evaluate('(el) => document.activeElement === el'), True)

    def test_legacy_section_fragments_redirect_without_losing_state(self):
        for fragment in ('protocol', 'submit', 'model-search'):
            for query in ('', '?setting=RH&task=all&code=0'):
                with self.subTest(fragment=fragment, query=query):
                    self.page.goto(self.base + query + '#' + fragment)
                    self.page.wait_for_url('**/leaderboard.html*')
                    self.page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'false'")
                    self.assertTrue(self.page.url.endswith(query + '#' + fragment))
                    self.assertEqual(self.page.locator('#' + fragment).count(), 1)
        self.page.goto(self.base + '#main-content')
        self.assertEqual(self.page.locator('body').get_attribute('data-page'), 'home')

    def test_unavailable_training_code_is_not_a_link_in_model_details(self):
        for availability, url, linked in (
            ('available', 'https://example.org/training', True),
            ('unavailable', 'https://example.org/training', False),
            ('unavailable', None, False),
            ('available', 'javascript:alert(1)', False),
        ):
            with self.subTest(availability=availability, url=url):
                self.page.unroute('**/data/leaderboard.json')
                payload = copy.deepcopy(self.payload)
                payload['submissions'][0]['code_availability'] = availability
                payload['submissions'][0]['training_code_url'] = url
                self.with_payload(payload)
                self.open()
                name = payload['submissions'][0]['model_name']
                self.page.get_by_role('button', name='View details for ' + name, exact=False).first.click()
                row = self.page.locator('#dialog-content dt').filter(has_text=re.compile('^Training code$')).locator('..')
                self.assertEqual(row.locator('a').count(), int(linked))
                if availability == 'unavailable':
                    self.assertIn('Not published', row.inner_text())

    def test_view_results_anchor_focuses_visible_controls(self):
        for width in (1280, 375):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open('?setting=RH&task=binary_node_classification')
                link = self.page.locator('#view-results-link')
                self.assertEqual(link.count(), 1, 'The page intro needs a direct route to the comparison controls')
                self.assertEqual(link.get_attribute('href'), '#leaderboard-controls')
                link.focus()
                link.press('Enter')
                self.page.wait_for_function("document.activeElement.id === 'leaderboard-controls'")
                self.page.wait_for_function('''() => {
                    const controls=document.getElementById('leaderboard-controls').getBoundingClientRect();
                    const header=document.querySelector('.site-header').getBoundingClientRect();
                    return controls.top >= header.bottom && controls.bottom <= innerHeight;
                }''')
                controls_bounds = self.page.locator('#leaderboard-controls').bounding_box()
                self.assertTrue(self.page.url.endswith('#leaderboard-controls'))
                self.assertEqual(self.page.locator('#setting-tab-RH').get_attribute('aria-selected'), 'true')
                self.assertEqual(self.page.locator('#task-tab-binary_node_classification').get_attribute('aria-selected'), 'true')
                self.page.keyboard.press('Tab')
                self.assertEqual(self.page.evaluate('document.activeElement.id'), 'setting-tab-RH')
                self.brand_observations.append({'check': 'view_results_anchor', 'width': width,
                                                'focused_controls_id': 'leaderboard-controls',
                                                'next_tab_focus': self.page.evaluate('document.activeElement.id'),
                                                'controls': controls_bounds})

    def test_sort_keeps_keyboard_focus_and_numeric_order(self):
        self.open()
        header = self.page.get_by_role('button', name='H&M Categories Accuracy: sort descending', exact=True)
        header.focus()
        header.press('Space')
        header = self.page.get_by_role('button', name='H&M Categories Accuracy: sort ascending', exact=True)
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.sortKey'), 'hm-categories')
        self.assertEqual(header.locator('..').get_attribute('aria-sort'), 'descending')
        names = self.page.locator('#leaderboard-body .model-button strong').all_text_contents()
        expected = sorted(self.payload['submissions'], key=lambda item: (
            -next((r['value'] for r in item['results'] if r['dataset'] == 'hm-categories' and r['setting'] == 'RL'), -float('inf')),
            item['model_name']))
        self.assertEqual(names, [item['model_name'] for item in expected])
        header.press('Enter')
        header = self.page.get_by_role('button', name='H&M Categories Accuracy: sort descending', exact=True)
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

    def test_logo_header_keyboard_order_and_content_remain_available_across_breakpoints(self):
        self.open()
        self.page.keyboard.press('Tab')
        self.assertEqual(self.page.evaluate('document.activeElement.className'), 'skip-link')
        self.page.keyboard.press('Tab')
        self.assertTrue(self.page.locator('.wordmark').evaluate('(el) => el === document.activeElement'))
        for index, width in enumerate((320, 375, 767, 768, 999, 1000, 1600, 1601, 1920)):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.page.evaluate('async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); }')
                if index:
                    self.assertEqual(self.page.evaluate('document.activeElement.id'), 'model-search')
                self.assertFalse(self.page.locator('#main-content').evaluate('(el) => el.inert'))
                self.assertFalse(self.page.locator('.site-footer').evaluate('(el) => el.inert'))
                self.assertFalse(self.page.locator('body').evaluate('(el) => el.classList.contains("nav-open")'))
                self.assertEqual(self.page.locator('.site-header button, .site-header nav').count(), 0)
                self.page.locator('.wordmark').focus()
                self.page.keyboard.press('Tab')
                self.assertTrue(self.page.locator('.project-navigation a').first.evaluate('(el) => el === document.activeElement'))
                self.page.keyboard.press('Shift+Tab')
                self.assertTrue(self.page.locator('.wordmark').evaluate('(el) => el === document.activeElement'))
                outline = self.page.locator('.wordmark').evaluate('''(el) => {
                    const style=getComputedStyle(el);
                    return {style:style.outlineStyle,width:parseFloat(style.outlineWidth)};
                }''')
                self.assertNotEqual(outline['style'], 'none')
                self.assertGreater(outline['width'], 0)
                for link in self.page.locator('.project-navigation a').all():
                    self.page.keyboard.press('Tab')
                    self.assertTrue(link.evaluate('(el) => el === document.activeElement'))
                self.page.keyboard.press('Tab')
                self.assertEqual(self.page.evaluate('document.activeElement.id'), 'submit-results-link')
                self.page.locator('#model-search').focus()
                self.page.keyboard.press('Escape')
                self.assertEqual(self.page.evaluate('document.activeElement.id'), 'model-search')
                self.brand_observations.append({'check': 'logo_header_keyboard_no_inert', 'width': width,
                    'tab_order': 'skip link → logo → project page links → Submit', 'logo_focus_outline': outline,
                    'focused_content_id': 'model-search', 'content_inert': False})

    def test_selected_task_is_visible_on_deep_links_history_and_resize(self):
        for width in (320, 375, 560, 767, 768):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 800})
                self.open('?task=all')
                self.page.wait_for_function('''()=>{
                    const tab=document.querySelector('#task-tabs [aria-selected="true"]').getBoundingClientRect();
                    const view=document.querySelector('.task-control').getBoundingClientRect();
                    return tab.left>=view.left-1&&tab.right<=view.right+1;
                }''')
                self.assertEqual(self.page.evaluate('scrollY'), 0)
                self.page.locator('#task-tab-all').focus()
                self.page.keyboard.press('Home')
                self.assertEqual(self.page.locator('#task-tab-multiclass_node_classification').get_attribute('aria-selected'), 'true')
                self.page.keyboard.press('End')
                self.assertEqual(self.page.locator('#task-tab-all').get_attribute('aria-selected'), 'true')
                self.page.go_back()
                self.page.wait_for_function("document.querySelector('#task-tab-multiclass_node_classification').getAttribute('aria-selected')==='true'")
                self.page.wait_for_function("document.querySelector('.task-control').scrollLeft<1")
        self.page.set_viewport_size({'width': 1920, 'height': 900})
        self.open('?task=all')
        self.page.set_viewport_size({'width': 320, 'height': 800})
        self.page.wait_for_function('''()=>{
            const t=document.querySelector('#task-tab-all').getBoundingClientRect(),v=document.querySelector('.task-control').getBoundingClientRect();
            return t.left>=v.left-1&&t.right<=v.right+1;
        }''')
        self.assertEqual(self.page.evaluate('scrollY'), 0)

    def test_selected_task_remains_visible_after_text_spacing_changes(self):
        self.page.set_viewport_size({'width': 320, 'height': 800})
        self.open('?task=all')
        self.page.add_style_tag(content='''
            * { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; }
            p { margin-bottom: 2em !important; }
        ''')
        # Let the new styles and ResizeObserver delivery settle before inspecting selection.
        self.page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        self.page.wait_for_function('''()=>{
            const tab=document.querySelector('#task-tab-all').getBoundingClientRect();
            const view=document.querySelector('.task-control').getBoundingClientRect();
            return tab.left>=view.left-1&&tab.right<=view.right+1;
        }''')
        self.assertEqual(self.page.evaluate('scrollY'), 0)
        self.assertEqual(self.page.locator('#task-tab-all').get_attribute('aria-selected'), 'true')
        # Looking back through other tasks must not snap to the selected one.
        self.page.locator('.task-control').evaluate('(e)=>e.scrollLeft=0')
        self.page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        self.assertEqual(self.page.locator('.task-control').evaluate('(e)=>e.scrollLeft'), 0)
        self.assertEqual(self.page.evaluate('scrollY'), 0)

    def test_tablist_preserves_modified_keys(self):
        self.open()
        tab = self.page.locator('#task-tab-multiclass_node_classification')
        tab.focus()
        for modifiers in ({'ctrlKey': True}, {'altKey': True}, {'metaKey': True}, {'shiftKey': True}, {'isComposing': True}):
            result = tab.evaluate('''(el,modifiers)=>{
                const event=new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true,...modifiers});
                el.dispatchEvent(event);return {prevented:event.defaultPrevented,selected:el.getAttribute('aria-selected')};
            }''', modifiers)
            self.assertFalse(result['prevented'])
            self.assertEqual(result['selected'], 'true')

    def test_opening_another_model_starts_details_at_the_top(self):
        self.page.set_viewport_size({'width': 375, 'height': 600})
        self.open()
        models = self.page.locator('.model-button')
        models.first.click()
        viewport = self.page.locator('.dialog-shell')
        viewport.evaluate('(e)=>e.scrollTop=e.scrollHeight')
        self.assertGreater(viewport.evaluate('(e)=>e.scrollTop'), 100)
        self.page.locator('.dialog-close').click()
        models.nth(1).click()
        self.assertEqual(viewport.evaluate('(e)=>e.scrollTop'), 0)
        self.assertTrue(self.page.locator('.dialog-lead').is_visible())

    def test_dialog_keyboard_links_are_visible_below_toolbar(self):
        for width, height in ((320, 568), (375, 400), (844, 390), (1280, 900)):
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.open()
                self.page.locator('.model-button').first.click()
                self.page.locator('.dialog-close').focus()
                count = self.page.locator('#model-dialog button,#model-dialog a').count()
                link_count = self.page.locator('#model-dialog a').count()
                visited_links = set()
                for direction in ('Tab', 'Shift+Tab'):
                    for _ in range(count + 1):
                        self.page.keyboard.press(direction)
                        self.page.wait_for_function('''()=>{
                            const e=document.activeElement,d=document.querySelector('#model-dialog');
                            if(!d.contains(e)||!e.matches('a,button'))return true;
                            const r=e.getBoundingClientRect(),h=d.querySelector('.dialog-header'),b=d.getBoundingClientRect();
                            return r.top>=(h.contains(e)?b.top:h.getBoundingClientRect().bottom)-1&&r.bottom<=b.bottom+1;
                        }''')
                        index = self.page.evaluate("Array.from(document.querySelectorAll('#model-dialog a')).indexOf(document.activeElement)")
                        if index >= 0:
                            visited_links.add(index)
                self.assertEqual(visited_links, set(range(link_count)), 'Every dialog link must actually receive keyboard focus')
                self.page.keyboard.press('Escape')

    def test_dialog_pointer_hold_keeps_edge_links_stationary(self):
        self.page.set_viewport_size({'width': 375, 'height': 400})
        for keyboard_first in (False, True):
            for edge in ('top', 'bottom'):
                with self.subTest(keyboard_first=keyboard_first, edge=edge):
                    self.open()
                    self.page.locator('.model-button').first.click()
                    if keyboard_first:
                        self.page.locator('.dialog-close').focus()
                        self.page.keyboard.press('Tab')
                        self.page.locator('.dialog-close').focus()
                    link = self.page.locator('#dialog-content a').first
                    point = link.evaluate('''(link,edge)=>{
                        const s=link.closest('.dialog-shell'),h=link.closest('dialog').querySelector('.dialog-header');
                        window.dialogLinkClicks=0;
                        link.addEventListener('click',e=>{e.preventDefault();window.dialogLinkClicks++});
                        s.scrollTop=500;let r=link.getBoundingClientRect();
                        const desired=edge==='top'?h.getBoundingClientRect().bottom-r.height+4:s.getBoundingClientRect().bottom-4;
                        s.scrollTop+=r.top-desired;r=link.getBoundingClientRect();
                        return {x:r.left+30,y:edge==='top'?r.bottom-2:r.top+2,top:r.top,scroll:s.scrollTop};
                    }''', edge)
                    self.page.mouse.move(point['x'], point['y'])
                    self.page.mouse.down()
                    self.page.wait_for_timeout(180)
                    self.assertEqual(self.page.locator('.dialog-shell').evaluate('(e)=>e.scrollTop'), point['scroll'])
                    self.assertEqual(link.bounding_box()['y'], point['top'])
                    self.page.mouse.up()
                    self.assertEqual(self.page.evaluate('window.dialogLinkClicks'), 1)
                    self.page.keyboard.press('Escape')

    def test_dialog_focus_correction_ignores_stale_callbacks(self):
        self.page.set_viewport_size({'width': 375, 'height': 400})
        self.open()
        self.page.locator('.model-button').first.click()
        self.page.locator('.dialog-close').focus()
        self.page.keyboard.press('Tab')
        result = self.page.evaluate('''()=>{
            const s=document.querySelector('.dialog-shell'),link=s.querySelector('a'),close=document.querySelector('.dialog-close');
            const original=requestAnimationFrame,queue=[];
            window.requestAnimationFrame=cb=>{queue.push(cb);return 0};
            try {
                close.focus({preventScroll:true});link.focus();
                s.scrollTop=70;close.focus({preventScroll:true});
                const before=s.scrollTop;queue.forEach(cb=>cb());
                return {queued:queue.length,before,after:s.scrollTop};
            } finally {window.requestAnimationFrame=original;}
        }''')
        self.assertGreater(result['queued'], 0)
        self.assertEqual(result['before'], result['after'])

    def test_long_model_title_does_not_block_short_dialog_content(self):
        payload = copy.deepcopy(self.payload)
        name = 'LongModel' + 'W' * 111  # Maximum length accepted by the submission schema.
        payload['submissions'][0]['model_name'] = name
        self.with_payload(payload)
        for width, height in ((320, 400), (375, 400), (844, 390), (1280, 900)):
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.open()
                self.page.locator('.model-button').filter(has_text=name).click()
                self.assertEqual(self.page.locator('#dialog-title').inner_text(), name)
                close = self.page.locator('.dialog-close')
                initial_top = close.bounding_box()['y']
                viewport = self.page.locator('.dialog-shell')
                self.assertGreaterEqual(viewport.bounding_box()['height'], 200)
                self.assertFalse(viewport.evaluate('(e)=>e.scrollWidth>e.clientWidth'))
                close.focus()
                for _ in range(4):
                    self.page.keyboard.press('Tab')
                    if self.page.evaluate("document.activeElement.matches('#dialog-content a')"):
                        break
                self.assertTrue(self.page.evaluate("document.activeElement.matches('#dialog-content a')"))
                self.page.wait_for_function('''()=>{
                    const r=document.activeElement.getBoundingClientRect(),h=document.querySelector('.dialog-header').getBoundingClientRect(),d=document.querySelector('#model-dialog').getBoundingClientRect();
                    return r.top>=h.bottom-1&&r.bottom<=d.bottom+1;
                }''')
                viewport.evaluate('(e)=>e.scrollTop=e.scrollHeight')
                self.assertEqual(close.bounding_box()['y'], initial_top)
                close.click()
                self.assertFalse(self.page.locator('#model-dialog').is_visible())

    def test_switch_and_current_page_survive_forced_colors(self):
        self.page.set_viewport_size({'width': 375, 'height': 800})
        for mode, scheme in (('none', 'light'), ('active', 'light'), ('active', 'dark')):
            self.page.emulate_media(forced_colors=mode, color_scheme=scheme, reduced_motion='reduce')
            self.open()
            for checked in (False, True):
                self.page.locator('#code-filter').set_checked(checked)
                colors = self.page.locator('.toggle').evaluate('''e=>{
                    const track=getComputedStyle(e.querySelector('.toggle-track')),thumb=getComputedStyle(e.querySelector('.toggle-track span'));
                    return {track:track.backgroundColor,thumb:thumb.backgroundColor,canvas:getComputedStyle(document.body).backgroundColor,border:track.borderStyle,borderWidth:parseFloat(track.borderWidth)};
                }''')
                def composite(css, background):
                    parts = [float(n) for n in re.findall(r'[\d.]+', css)]
                    alpha = parts[3] if len(parts) > 3 else 1
                    return [n*alpha+b*(1-alpha) for n,b in zip(parts[:3],background)]
                def luminance(rgb):
                    channels = [n/255 for n in rgb]
                    linear = [n/12.92 if n <= .04045 else ((n+.055)/1.055)**2.4 for n in channels]
                    return sum(n*w for n,w in zip(linear,(.2126,.7152,.0722)))
                canvas = composite(colors['canvas'], [255,255,255])
                track = composite(colors['track'], canvas)
                thumb = composite(colors['thumb'], track)
                lighter, darker = sorted([luminance(track), luminance(thumb)], reverse=True)
                self.assertGreaterEqual((lighter+.05)/(darker+.05), 3)
                if mode == 'active':
                    self.assertEqual(colors['border'], 'solid')
                    self.assertGreaterEqual(colors['borderWidth'], 1)
            if mode == 'active':
                self.assertEqual(self.page.locator('.wordmark').evaluate('(e)=>getComputedStyle(e).backgroundColor'), 'rgb(255, 255, 255)')
                self.assertIn('underline', self.page.locator('.project-navigation [aria-current]').evaluate('(e)=>getComputedStyle(e).textDecorationLine'))
                for link in self.page.locator('.project-navigation a:not([aria-current])').all():
                    self.assertEqual(link.evaluate('(e)=>getComputedStyle(e).borderBottomWidth'), '0px')
                    self.assertNotIn('underline', link.evaluate('(e)=>getComputedStyle(e).textDecorationLine'))
                for tab in self.page.locator('#setting-tabs button').all():
                    expected_width = '2px' if tab.get_attribute('aria-selected') == 'true' else '0px'
                    self.assertEqual(tab.evaluate('(e)=>getComputedStyle(e).borderBottomWidth'), expected_width)
                self.page.locator('#model-search').focus()
                self.page.keyboard.press('Tab')
                self.assertEqual(self.page.evaluate('document.activeElement.id'), 'code-filter')
                self.assertNotEqual(self.page.locator('.toggle-track').evaluate('(e)=>getComputedStyle(e).outlineStyle'), 'none')
                self.page.keyboard.press('Space')
                self.assertFalse(self.page.locator('#code-filter').is_checked())
                self.page.locator('.model-button').first.click()
                close_colors = self.page.locator('.dialog-close').evaluate('''e=>({background:getComputedStyle(e).backgroundColor,icon:getComputedStyle(e.querySelector('span')).backgroundColor,border:getComputedStyle(e).borderStyle})''')
                close_background = composite(close_colors['background'], canvas)
                close_icon = composite(close_colors['icon'], close_background)
                lighter, darker = sorted([luminance(close_background), luminance(close_icon)], reverse=True)
                self.assertGreaterEqual((lighter+.05)/(darker+.05), 3)
                self.assertEqual(close_colors['border'], 'solid')
                self.page.locator('.dialog-close').click()
                self.assertFalse(self.page.locator('#model-dialog').is_visible())

    def test_three_empty_states_and_filter_focus(self):
        payload = copy.deepcopy(self.payload)
        payload['submissions'] = []
        self.with_payload(payload)
        self.open()
        self.assertEqual(self.page.locator('#empty-state h3').inner_text(), 'No results yet')
        self.assertTrue(self.page.locator('#empty-state a').is_visible())
        self.assertFalse(self.page.locator('#result-footnote').is_visible())
        self.page.unroute('**/data/leaderboard.json')
        payload['submissions'] = [copy.deepcopy(self.payload['submissions'][0])]
        payload['submissions'][0]['results'] = [{'setting': 'RL', 'dataset': 'city-reviews', 'value': .7}]
        self.with_payload(payload)
        self.open('?task=node_regression')
        self.assertIn('No results for this setting', self.page.locator('#empty-state h3').inner_text())
        self.assertTrue(self.page.locator('#empty-state a').is_visible())
        self.assertFalse(self.page.locator('#result-footnote').is_visible())
        self.page.unroute('**/data/leaderboard.json')
        self.open()
        self.page.locator('#model-search').fill('no-such-model')
        self.assertEqual(self.page.locator('#empty-state h3').inner_text(), 'No models match these filters')
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'model-search')
        self.assertFalse(self.page.locator('#empty-state a').is_visible(),
                         'Hidden submission link must not be revealed by inline-flex styles')
        self.assertFalse(self.page.locator('#result-footnote').is_visible())
        self.page.locator('#reset-filters').click()
        self.assertTrue(self.page.locator('#result-footnote').is_visible())
        self.assertFalse(self.page.locator('#empty-state').is_visible())

    def test_compact_controls_have_full_click_targets(self):
        for width in (320, 375, 767, 1280, 1920):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open()
                toggle = self.page.locator('.toggle')
                self.assertGreaterEqual(toggle.bounding_box()['height'], 44)
                self.page.locator('#code-filter').uncheck()
                # The padding above the visual track must also activate the label.
                toggle.click(position={'x': 10, 'y': 2})
                self.assertTrue(self.page.locator('#code-filter').is_checked())
                self.page.locator('#model-search').fill('no-such-model')
                reset = self.page.locator('#reset-filters')
                self.assertGreaterEqual(reset.bounding_box()['height'], 44)
                if width <= 767:
                    self.assertGreaterEqual(self.page.locator('.search-field').bounding_box()['height'], 44)
                reset.click(position={'x': 10, 'y': 2})
                self.assertEqual(self.page.locator('#model-search').input_value(), '')
                self.page.locator('.model-button').first.click()
                close = self.page.locator('.dialog-close')
                bounds = close.bounding_box()
                self.assertGreaterEqual(bounds['width'], 44)
                self.assertGreaterEqual(bounds['height'], 44)
                close.click(position={'x': 22, 'y': 2})
                self.assertFalse(self.page.locator('#model-dialog').is_visible())

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
        self.page.goto(self.base + 'leaderboard.html')
        self.page.wait_for_selector('#load-error', state='visible')
        self.assertFalse(self.page.locator('#result-footnote').is_visible())
        self.assertEqual(self.page.locator('#load-error a[href="leaderboard.xlsx"]').count(), 1)
        self.assertEqual(self.page.locator('#load-error a[href="leaderboard.csv"]').count(), 1)
        self.page.locator('#retry-load').click()
        self.page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'false'")
        self.assertFalse(self.page.locator('#load-error').is_visible())
        self.assertTrue(self.page.locator('#leaderboard-body tr').count() > 0)
        self.assertEqual(len(requests), 2)
        self.assertTrue(self.page.locator('#result-footnote').is_visible())

    def test_no_page_overflow_on_common_viewports(self):
        for width in (320, 375, 768, 1280):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open()
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))

    def hold_retry_after_failure(self):
        self.page.emulate_media(reduced_motion='reduce')
        requests, pending = [], []
        def respond(route):
            requests.append(route.request.url)
            if len(requests) == 1:
                route.fulfill(status=503, body='unavailable')
            else:
                pending.append(route)
        self.page.route('**/data/leaderboard.json', respond)
        self.page.goto(self.base + 'leaderboard.html')
        self.page.wait_for_selector('#load-error', state='visible')
        self.page.locator('#retry-load').focus()
        with self.page.expect_request('**/data/leaderboard.json'):
            self.page.keyboard.press('Enter')
        self.page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'true'")
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'retry-load')
        self.assertEqual(self.page.locator('#retry-load').get_attribute('aria-disabled'), 'true')
        self.assertEqual(self.page.locator('#retry-load').inner_text(), 'Loading…')
        self.assertTrue(self.page.locator('#load-error a[href="leaderboard.csv"]').is_visible())
        self.assertEqual(len(pending), 1)
        return requests, pending[0]

    def test_retry_keeps_focus_pending_and_moves_it_to_loaded_results(self):
        requests, pending = self.hold_retry_after_failure()
        self.page.keyboard.press('Enter')
        bounds = self.page.locator('#retry-load').bounding_box()
        self.page.mouse.click(bounds['x'] + 10, bounds['y'] + 10)
        self.page.evaluate('()=>new Promise(requestAnimationFrame)')
        self.assertEqual(len(requests), 2)
        pending.fulfill(json=self.payload)
        self.page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'false'")
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'leaderboard-panel')
        self.assertFalse(self.page.locator('#load-error').is_visible())
        self.page.keyboard.press('Tab')
        self.assertTrue(self.page.evaluate("document.activeElement.closest('#leaderboard-panel') !== null"))
        self.assertNotEqual(self.page.evaluate('document.activeElement.id'), 'leaderboard-panel')

    def test_retry_failure_retains_usable_retry_and_downloads(self):
        requests, pending = self.hold_retry_after_failure()
        pending.fulfill(status=503, body='still unavailable')
        self.page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'false'")
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'retry-load')
        self.assertIsNone(self.page.locator('#retry-load').get_attribute('aria-disabled'))
        self.assertEqual(self.page.locator('#retry-load').inner_text(), 'Retry loading')
        self.assertTrue(self.page.locator('#load-error a[href="leaderboard.csv"]').is_visible())
        self.assertEqual(len(requests), 2)

    def test_retry_does_not_take_focus_back_from_another_section(self):
        _, pending = self.hold_retry_after_failure()
        elsewhere = self.page.locator('.project-navigation a[href="datasets.html"]')
        elsewhere.focus()
        scroll_y = self.page.evaluate('scrollY')
        pending.fulfill(json=self.payload)
        self.page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'false'")
        self.assertTrue(elsewhere.evaluate('(e)=>e===document.activeElement'))
        self.assertEqual(self.page.evaluate('scrollY'), scroll_y)

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
                cell = row.locator('td[data-state]').first
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

    def test_project_pages_navigate_natively_and_restore_current_page_with_history(self):
        pages = [('index.html', 'Home', 'home', 'GraphLand'),
                 ('datasets.html', 'Datasets', 'datasets', 'Datasets'),
                 ('leaderboard.html', 'Leaderboard', 'leaderboard', 'Leaderboard'),
                 ('getting-started.html', 'Getting Started', 'getting-started', 'Getting Started')]
        visits = []
        self.brand_observations.append({'check': 'four_native_pages_and_history', 'visits': visits})
        for width in (320, 375, 1280, 1920):
            with self.subTest(width=width):
                shared_styles = None
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open()
                for path, label, marker, heading in pages:
                    nav = self.page.locator('.project-navigation')
                    self.assertEqual(nav.locator('a').all_text_contents(), [entry[1] for entry in pages])
                    self.assertEqual(self.page.locator('.site-header .project-navigation').count(), 0)
                    self.page.evaluate('window.__nativeProjectDocumentMarker = true')
                    with self.page.expect_navigation(wait_until='domcontentloaded') as navigation:
                        nav.get_by_role('link', name=label, exact=True).click()
                    self.assertEqual(navigation.value.status, 200)
                    self.page.evaluate('async () => { await document.fonts.ready; }')
                    if marker == 'leaderboard':
                        self.page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy') === 'false'")
                    self.assertEqual(self.page.url, self.base + path)
                    self.assertEqual(self.page.evaluate('typeof window.__nativeProjectDocumentMarker'), 'undefined')
                    self.assertEqual(self.page.locator('body').get_attribute('data-page'), marker)
                    current = self.page.locator('.project-navigation [aria-current="page"]')
                    self.assertEqual(current.count(), 1)
                    self.assertEqual(current.inner_text(), label)
                    self.page.wait_for_function('''() => {
                        const nav=document.querySelector('.project-navigation').getBoundingClientRect();
                        const link=document.querySelector('.project-navigation [aria-current="page"]').getBoundingClientRect();
                        return link.left>=Math.max(0,nav.left)-1 && link.right<=Math.min(innerWidth,nav.right)+1;
                    }''', timeout=5000)
                    if width <= 560:
                        links = self.page.locator('.project-navigation a').evaluate_all(
                            '(nodes)=>nodes.map(el=>el.getBoundingClientRect().toJSON())')
                        self.assertEqual(len(links), 4)
                        self.assertTrue(all(rect['left'] >= 0 and rect['right'] <= width and rect['height'] >= 44
                                            for rect in links), 'Every mobile section link must be fully visible and tappable')
                    self.assertLessEqual(abs(self.page.evaluate('scrollY')), 1,
                                         'Revealing the current section must not jump vertically')
                    self.assertEqual(self.page.locator('h1').count(), 1)
                    self.assertEqual(heading, self.page.locator('h1').inner_text())
                    self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    h2_selector = {'home': '.page-section > h2', 'datasets': '.dataset-description h2',
                                   'leaderboard': '#legend-title', 'getting-started': '.page-section > h2'}[marker]
                    styles = self.page.evaluate(r'''(h2Selector) => {
                        const measure=el=>{
                            const s=getComputedStyle(el);
                            return {family:s.fontFamily,size:parseFloat(s.fontSize),line:parseFloat(s.lineHeight),
                                weight:Number(s.fontWeight),tracking:s.letterSpacing};
                        };
                        const intro=document.querySelector('.page-intro');
                        const introStyle=getComputedStyle(intro);
                        const nav=document.querySelector('.project-navigation');
                        const normal=nav.querySelector('a:not([aria-current="page"])');
                        const active=nav.querySelector('[aria-current="page"]');
                        const activeStyle=getComputedStyle(active);
                        const h2=document.querySelector(h2Selector);
                        const h2Bounds=h2.getBoundingClientRect();
                        const leadStyle=getComputedStyle(intro.querySelector('.page-lead'));
                        const borderColor=activeStyle.borderBottomColor;
                        const transparentBorder=borderColor==='transparent'
                            || /^rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(borderColor);
                        return {h1:measure(intro.querySelector('h1')),lead:measure(intro.querySelector('.page-lead')),
                            h2:measure(h2),h2Selector,h2Renderable:h2Bounds.width>1 && h2Bounds.height>1
                                && getComputedStyle(h2).visibility==='visible',
                            nav:measure(normal),activeNav:measure(active),
                            introPadding:{top:introStyle.paddingTop,bottom:introStyle.paddingBottom},
                            leadMargin:{top:leadStyle.marginTop,bottom:leadStyle.marginBottom},
                            activeColor:activeStyle.color,normalColor:getComputedStyle(normal).color,
                            activeDecoration:activeStyle.textDecorationLine,
                            activeBorderWidth:parseFloat(activeStyle.borderBottomWidth),
                            activeBorderColor:borderColor,
                            activeBorderVisible:parseFloat(activeStyle.borderBottomWidth)>0
                                && activeStyle.borderBottomStyle!=='none' && !transparentBorder,
                            activeBounds:active.getBoundingClientRect().toJSON()};
                    }''', h2_selector)
                    visits.append({'width': width, 'page': marker, 'native_navigation_status': 200, 'current': label,
                                   'current_section_visible_without_vertical_jump': True, 'styles': styles})
                    h1_size, h1_line, lead_size, nav_size, h2_size = (
                        (48, 48, 22, 20, 32) if width > 1600 else
                        (40, 40, 17, 16, 24) if width >= 768 else (30, 32.4, 15, 16, 24))
                    self.assertAlmostEqual(styles['h1']['size'], h1_size, delta=.05)
                    self.assertAlmostEqual(styles['h1']['line'], h1_line, delta=.05)
                    self.assertEqual(styles['h1']['weight'], 500)
                    self.assertAlmostEqual(styles['lead']['size'], lead_size, delta=.05)
                    self.assertIn('Merriweather', styles['lead']['family'])
                    self.assertTrue(styles['h2Renderable'], 'Compare rendered content headings, not visually hidden labels')
                    self.assertAlmostEqual(styles['h2']['size'], h2_size, delta=.05)
                    self.assertAlmostEqual(styles['nav']['size'], nav_size, delta=.05)
                    self.assertEqual(styles['nav']['weight'], 400)
                    self.assertEqual(styles['activeNav']['weight'], 400)
                    self.assertEqual(styles['activeDecoration'], 'none')
                    self.assertFalse(styles['activeBorderVisible'])
                    active_rgb = [int(number) for number in re.findall(r'\d+', styles['activeColor'])[:3]]
                    normal_rgb = [int(number) for number in re.findall(r'\d+', styles['normalColor'])[:3]]
                    self.assertEqual(len(set(active_rgb)), 1, 'The current section uses neutral gray')
                    self.assertGreater(active_rgb[0], normal_rgb[0])
                    self.assertLess(active_rgb[0], 255)
                    self.assertEqual(styles['leadMargin']['top'], '32px' if width > 1600 else '20px')
                    parity = {key: styles[key] for key in ('h1', 'lead', 'h2', 'nav', 'activeNav', 'introPadding', 'leadMargin')}
                    if shared_styles is None:
                        shared_styles = parity
                    else:
                        for component, metrics in parity.items():
                            self.assertEqual(metrics, shared_styles[component],
                                             f'Shared {component} style differs on {path} at {width}px')
                    if marker == 'home':
                        self.assertEqual(self.page.locator('.dataset-catalog tbody tr').count(), 14)
                        self.assertEqual(self.page.locator('.dataset-catalog thead th').count(), 9)
                    elif marker == 'datasets':
                        self.assertEqual(self.page.locator('article.dataset-description').count(), 9)
                    elif marker == 'getting-started':
                        self.assertEqual(self.page.locator('pre code.language-python').count(), 2)
                self.page.go_back()
                self.page.wait_for_url(self.base + 'leaderboard.html')
                self.assertEqual(self.page.locator('.project-navigation [aria-current="page"]').inner_text(), 'Leaderboard')
                self.page.go_forward()
                self.page.wait_for_url(self.base + 'getting-started.html')
                self.assertEqual(self.page.locator('.project-navigation [aria-current="page"]').inner_text(), 'Getting Started')

    def test_editorial_sections_tables_and_actions_share_styles_across_pages(self):
        self.page.emulate_media(reduced_motion='reduce')
        def style(selector):
            return self.page.locator(selector).first.evaluate("""el => {
                const s=getComputedStyle(el);
                return {family:s.fontFamily,size:s.fontSize,weight:s.fontWeight,line:s.lineHeight,
                    color:s.color,background:s.backgroundColor,radius:s.borderRadius,
                    border:s.borderTopColor,borderWidth:s.borderTopWidth,height:s.minHeight};
            }""")

        for width in (375, 1280, 1920):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.page.goto(self.base + 'index.html')
                self.page.evaluate('async()=>{await document.fonts.ready}')
                body_style = style('.article-copy > p')
                action_style = style('.page-actions .button')
                table_style = style('.dataset-catalog thead th')
                self.page.locator('.page-actions .button').first.hover()
                self.page.wait_for_function("getComputedStyle(document.querySelector('.page-actions .button')).backgroundColor==='rgb(26, 26, 26)'")
                hover_style = style('.page-actions .button')
                self.open()
                download = '.page-actions a[download]'
                self.page.mouse.move(0, 0)
                self.assertEqual(style(download), action_style)
                self.assertEqual(style('#view-results-link'), action_style)
                self.page.locator(download).hover()
                self.page.wait_for_function("getComputedStyle(document.querySelector('.page-actions a[download]')).backgroundColor==='rgb(26, 26, 26)'")
                self.assertEqual(style(download), hover_style)
                self.page.keyboard.press('Tab')
                self.page.locator(download).focus()
                focus = self.page.locator(download).evaluate('(el)=>getComputedStyle(el).outlineStyle')
                self.assertNotEqual(focus, 'none')
                self.page.mouse.move(0, 0)
                self.page.locator('.protocol-disclosure > summary').click()
                for selector in ('.legend-list dd', '.protocol-grid p', '.protocol-callout p', '.steps-list p'):
                    actual = style(selector)
                    for key in ('family', 'size', 'weight', 'line', 'color'):
                        self.assertEqual(actual[key], body_style[key], (selector, key, width))
                for selector in ('.legend-section', '.protocol-section', '.submit-section'):
                    section = self.page.locator(selector)
                    actual = style(selector)
                    self.assertIn(actual['background'], ('rgba(0, 0, 0, 0)', 'rgb(255, 255, 255)'))
                    self.assertEqual(actual['color'], body_style['color'])
                    self.assertEqual(section.evaluate('(el)=>getComputedStyle(el).marginTop'),
                                     self.page.locator('.legend-section').evaluate('(el)=>getComputedStyle(el).marginTop'))
                for selector in ('#leaderboard-table thead th', '#leaderboard-table th.metadata-column'):
                    if self.page.locator(selector).first.is_visible():
                        actual = style(selector)
                        for key in ('family', 'size', 'weight', 'color', 'background'):
                            self.assertEqual(actual[key], table_style[key], (selector, key, width))
                primary = style('#setting-tabs [aria-selected="true"]')
                secondary = style('#task-tabs [aria-selected="true"]')
                self.assertEqual(primary, secondary)
                self.assertEqual(self.page.locator('#task-tabs').get_attribute('aria-labelledby'), 'task-tabs-label')
                self.brand_observations.append({'check':'full_page_shared_components','width':width,
                    'prose':body_style,'action':action_style,'table_heading':table_style,
                    'white_editorial_sections':True,'matching_filter_tab_styles':True})

    def test_catalog_and_leaderboard_share_table_styles_and_focus_highlighting(self):
        self.page.emulate_media(reduced_motion='reduce')
        def styles(locator):
            return locator.evaluate("""e=>{const s=getComputedStyle(e);return {
                font:s.fontFamily,size:s.fontSize,line:s.lineHeight,weight:s.fontWeight,
                color:s.color,background:s.backgroundColor,padding:s.padding,
                border:s.borderBottom,vertical:s.verticalAlign}}""")
        for width in (320, 560, 561, 1280, 1920):
            self.page.set_viewport_size({'width':width,'height':900})
            baseline = None
            for route, selector in (('index.html','.dataset-catalog'), ('leaderboard.html','#leaderboard-table')):
                self.page.goto(self.base + route)
                self.page.evaluate('document.fonts.ready')
                table = self.page.locator(selector)
                table.locator('tbody tr').first.wait_for()
                self.page.mouse.move(0, 0)
                header = styles(table.locator('thead th').first)
                value = styles(table.locator('tbody td:nth-child(2)' if route=='index.html' else 'tbody .metric-value').first)
                first_cell = styles(table.locator('tbody tr').first.locator('th,td').first)
                if baseline is None:
                    baseline = (header, value, first_cell)
                    for column in (2,3,5,6):
                        self.assertEqual(table.locator(f'thead th:nth-child({column})').evaluate('(e)=>getComputedStyle(e).textAlign'),'right')
                else:
                    self.assertEqual(header, baseline[0])
                    for key in ('font','size','line','color','padding','border','vertical'):
                        self.assertEqual(value[key], baseline[1][key], (key,width))
                        self.assertEqual(first_cell[key], baseline[2][key], (key,width))
                    for sort in table.locator('.sort-button').all():
                        self.assertGreaterEqual(sort.bounding_box()['height'],44)
                action = table.locator('tbody th a' if route=='index.html' else '.model-button').first
                action.focus()
                self.page.wait_for_function("(e)=>[...e.closest('tr').children].every(c=>getComputedStyle(c).backgroundColor==='rgb(245, 245, 245)')",arg=action.element_handle(),timeout=3000)
                colors = table.locator('tbody tr').first.locator('th,td').evaluate_all('(cells)=>cells.map(e=>getComputedStyle(e).backgroundColor)')
                self.assertTrue(all(color=='rgb(245, 245, 245)' for color in colors), (route,width,colors))
                self.assertNotEqual(action.evaluate('(e)=>getComputedStyle(e).outlineStyle'),'none')
                self.assertEqual(table.locator('caption').count(),1)
                self.assertEqual(table.locator('caption').evaluate('(e)=>e.getBoundingClientRect().width'),1)

    def test_held_pointer_click_on_clipped_table_controls_is_not_lost(self):
        self.page.emulate_media(reduced_motion='reduce')
        for kind in ('sort','link'):
            for edge in ('left','right'):
                self.open('?task=all')
                viewport = self.page.locator('.table-scroll')
                viewport.evaluate("e=>{e.style.width='400px';scrollTo(0,scrollY+e.getBoundingClientRect().top-80)}")
                control = self.page.locator('.sort-button').nth(8) if kind=='sort' else self.page.locator('.metadata-link').first
                key = control.get_attribute('data-sort-key')
                control.evaluate("""e=>{window.probeClicks=0;e.addEventListener('click',ev=>{
                    window.probeClicks++;if(e.tagName==='A')ev.preventDefault()})}""")
                point = control.evaluate("""(e,edge)=>{
                    document.activeElement.blur();const v=document.querySelector('.table-scroll'),b=v.getBoundingClientRect();
                    const first=e.closest('tr').cells[0],r=e.getBoundingClientRect();
                    const boundary=edge==='left'?first.getBoundingClientRect().right:b.left+v.clientWidth;
                    v.scrollLeft+=r.left-(edge==='left'?boundary-r.width+4:boundary-4);
                    const q=e.getBoundingClientRect();return{x:edge==='left'?boundary+2:boundary-2,y:q.y+q.height/2,scroll:v.scrollLeft};
                }""",edge)
                self.page.mouse.move(point['x'],point['y'])
                self.page.mouse.down()
                self.page.wait_for_timeout(180)  # Deliberately hold across multiple animation frames.
                self.assertEqual(viewport.evaluate('(e)=>e.scrollLeft'),point['scroll'])
                self.page.mouse.up()
                self.assertEqual(self.page.evaluate('window.probeClicks'),1)
                if kind=='sort':
                    self.assertEqual(self.page.locator(f'.sort-button[data-sort-key="{key}"]').locator('..').get_attribute('aria-sort'),'descending')

    def test_keyboard_table_controls_stay_visible_beside_sticky_column(self):
        self.page.emulate_media(reduced_motion='reduce')
        for width in (320, 560, 561, 1280):
            self.page.set_viewport_size({'width':width,'height':900})
            self.open('?task=all')
            controls = self.page.locator('#leaderboard-table button:visible,#leaderboard-table a:visible')
            for direction in ('Tab','Shift+Tab'):
                (controls.first if direction=='Tab' else controls.last).focus()
                for _ in range(controls.count()):
                    self.page.wait_for_function("""()=>{
                        const e=document.activeElement,cell=e.closest('th,td');
                        if(!cell||!e.closest('#leaderboard-table'))return false;
                        const viewport=document.querySelector('.table-scroll'),box=viewport.getBoundingClientRect();
                        const first=cell.parentElement.cells[0],r=e.getBoundingClientRect();
                        const left=cell===first?box.left:Math.max(box.left,first.getBoundingClientRect().right);
                        const at=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                        return r.left>=left-1&&r.right<=box.left+viewport.clientWidth+1&&(at===e||e.contains(at));
                    }""")
                    self.page.keyboard.press(direction)

    def test_horizontal_scroll_keys_do_not_capture_descendant_controls_or_shortcuts(self):
        self.page.set_viewport_size({'width': 375, 'height': 900})
        self.open()
        viewport = self.page.locator('.table-scroll')
        viewport.focus()
        self.page.keyboard.press('ArrowRight')
        self.page.wait_for_function("document.querySelector('.table-scroll').scrollLeft > 0")
        self.page.keyboard.press('ArrowLeft')
        self.page.wait_for_function("document.querySelector('.table-scroll').scrollLeft === 0")
        # These events must retain their native behavior; the handler owns only
        # plain horizontal arrows targeted at the scroll region itself.
        for modifier in ('altKey', 'ctrlKey', 'metaKey', 'shiftKey', 'isComposing'):
            self.assertTrue(viewport.evaluate("""(el, modifier) => el.dispatchEvent(
                new KeyboardEvent('keydown', {key:'ArrowRight',bubbles:true,cancelable:true,[modifier]:true}))""", modifier))
        control = self.page.locator('.sort-button').first
        self.assertTrue(control.evaluate("""el=>el.dispatchEvent(new KeyboardEvent(
            'keydown',{key:'ArrowRight',bubbles:true,cancelable:true}))"""))
        self.page.locator('#model-search').fill('no-such-model')
        self.assertTrue(self.page.locator('#empty-state').is_visible())

    def test_documentation_navigation_and_code_remain_readable_and_keyboard_accessible(self):
        self.page.emulate_media(reduced_motion='reduce')
        for width in (320, 767, 768, 1280, 1920):
            self.page.set_viewport_size({'width': width, 'height': 900})
            self.page.goto(self.base + 'datasets.html')
            self.page.evaluate('document.fonts.ready')
            links = self.page.locator('.dataset-index a')
            self.assertEqual(links.count(), 9)
            for link in links.all():
                bounds = link.bounding_box()
                self.assertGreaterEqual(bounds['height'], 44)
                self.assertGreaterEqual(bounds['x'], 0)
                self.assertLessEqual(bounds['x'] + bounds['width'], width)
                self.assertTrue(link.get_attribute('aria-label').startswith(link.inner_text()))
            target = self.page.locator('.dataset-index a[href="#hm"]')
            target.focus()
            self.page.keyboard.press('Enter')
            self.page.wait_for_url('**/datasets.html#hm')
            top = self.page.locator('#hm').bounding_box()['y']
            header_bottom = self.page.locator('.site-header').bounding_box()['height']
            self.assertGreaterEqual(top, header_bottom)
            self.assertLess(top, 900)
            self.page.goto(self.base + 'getting-started.html')
            self.page.evaluate('document.fonts.ready')
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            blocks = self.page.locator('.code-block')
            for block in blocks.all():
                self.assertGreaterEqual(block.evaluate('(el)=>parseFloat(getComputedStyle(el).fontSize)'), 13)
                if width >= 1280:
                    self.assertTrue(block.evaluate('(el)=>el.scrollWidth <= el.clientWidth'))
                block.focus()
                self.assertNotEqual(block.evaluate('(el)=>getComputedStyle(el).outlineStyle'), 'none')
                if block.evaluate('(el)=>el.scrollWidth>el.clientWidth'):
                    self.page.keyboard.press('ArrowRight')
                    self.page.wait_for_function('(el)=>el.scrollLeft>0', arg=block.element_handle())
            self.assertEqual(self.page.locator('#transductive-loading p code').all_text_contents(),
                             ['Data', 'train_mask', 'val_mask', 'test_mask'])

    def test_copy_actions_preserve_exact_code_and_citation_text(self):
        self.page.add_init_script('''Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
            writeText:async text=>{window.copyWrites=(window.copyWrites||[]).concat(text)}
        }});''')
        for route in ('getting-started.html', 'index.html'):
            self.page.goto(self.base + route)
            if route == 'index.html':
                self.page.locator('.citation-disclosure summary').click()
            blocks = self.page.locator('.code-example')
            self.assertEqual(blocks.count(), 3 if route == 'getting-started.html' else 1)
            for index, block in enumerate(blocks.all()):
                expected = block.locator('code').text_content()
                button = block.locator('.copy-code')
                self.assertGreaterEqual(button.bounding_box()['height'], 44)
                self.assertTrue(button.get_attribute('aria-label').startswith('Copy'))
                button.focus()
                self.page.keyboard.press('Enter')
                self.page.wait_for_function('(index)=>window.copyWrites?.length===index+1', arg=index)
                self.assertEqual(self.page.evaluate('window.copyWrites.at(-1)'), expected)
                self.assertEqual(block.locator('.copy-status').inner_text(), 'Copied')
                self.assertTrue(button.evaluate('(e)=>document.activeElement===e'))
                self.assertIsNone(button.get_attribute('aria-disabled'))
                if route == 'getting-started.html' and index == 0:
                    self.assertEqual(expected, 'python -m pip install torch-geometric pandas scikit-learn PyYAML')
                if route == 'index.html':
                    self.assertEqual(button.inner_text(), 'Copy citation')
                    self.assertTrue(expected.startswith('@inproceedings{bazhenov2025graphland,'))

    def test_copy_pending_blocks_duplicates_without_stealing_focus(self):
        self.page.add_init_script('''Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
            writeText:text=>{window.copyCalls=(window.copyCalls||0)+1;return new Promise(resolve=>window.resolveCopy=resolve)}
        }});''')
        self.page.goto(self.base + 'getting-started.html')
        block = self.page.locator('.code-example').first
        button = block.locator('.copy-code')
        button.focus()
        self.page.keyboard.press('Space')
        self.assertEqual(block.locator('.copy-status').inner_text(), 'Copying…')
        self.assertEqual(button.get_attribute('aria-disabled'), 'true')
        self.assertTrue(button.evaluate('(e)=>document.activeElement===e'))
        self.page.keyboard.press('Enter')
        self.assertEqual(self.page.evaluate('window.copyCalls'), 1)
        self.page.keyboard.press('Tab')
        self.assertTrue(block.locator('.code-block').evaluate('(e)=>document.activeElement===e'))
        self.page.evaluate('window.resolveCopy()')
        self.page.wait_for_function("document.querySelector('.copy-status').textContent==='Copied'")
        self.assertTrue(block.locator('.code-block').evaluate('(e)=>document.activeElement===e'))
        self.assertIsNone(button.get_attribute('aria-disabled'))

    def test_copy_failure_preserves_selection_and_allows_retry(self):
        self.page.set_viewport_size({'width': 320, 'height': 800})
        self.page.add_init_script('''Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
            writeText:async text=>{window.copyCalls=(window.copyCalls||0)+1;if(window.copyCalls===1)throw new Error('Denied')}
        }});''')
        self.page.goto(self.base + 'getting-started.html')
        block = self.page.locator('.code-example').first
        expected = block.locator('code').text_content()
        block.locator('code').evaluate('''e=>{const r=document.createRange();r.selectNodeContents(e);getSelection().removeAllRanges();getSelection().addRange(r)}''')
        button = block.locator('.copy-code')
        button.focus()
        self.page.keyboard.press('Enter')
        self.page.wait_for_function("document.querySelector('.copy-status').textContent.startsWith('Could not copy.')")
        self.assertEqual(self.page.evaluate('getSelection().toString()'), expected)
        self.assertTrue(button.evaluate('(e)=>document.activeElement===e'))
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth>innerWidth'))
        self.page.keyboard.press('Enter')
        self.page.wait_for_function("document.querySelector('.copy-status').textContent==='Copied'")
        self.assertEqual(self.page.evaluate('window.copyCalls'), 2)

    def test_code_remains_usable_without_clipboard_or_javascript(self):
        self.page.add_init_script("Object.defineProperty(navigator,'clipboard',{value:undefined})")
        self.page.goto(self.base + 'getting-started.html')
        self.assertEqual(self.page.locator('.copy-code').count(), 0)
        expected = self.page.locator('.code-block code').all_text_contents()
        self.assertEqual(len(expected), 3)
        no_js = self.browser.new_page(java_script_enabled=False)
        try:
            no_js.goto(self.base + 'getting-started.html')
            self.assertEqual(no_js.locator('.code-block code').all_text_contents(), expected)
            self.assertEqual(no_js.locator('.copy-code').count(), 0)
        finally:
            no_js.close()

    def test_model_dialog_uses_shared_typography_and_keeps_long_titles_inside(self):
        payload = copy.deepcopy(self.payload)
        payload['submissions'][0]['model_name'] = 'LongModel' + 'X' * 150
        self.with_payload(payload)
        for width in (320, 560, 561, 1280, 1920):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.open()
                shared = self.page.locator('#legend-title').evaluate("""el=>{
                    const s=getComputedStyle(el);return [s.fontSize,s.lineHeight,s.letterSpacing,s.fontWeight];
                }""")
                self.page.locator('.model-button').filter(has_text='LongModel').click()
                dialog = self.page.locator('#model-dialog')
                self.assertTrue(dialog.is_visible())
                actual = self.page.locator('#dialog-title').evaluate("""el=>{
                    const s=getComputedStyle(el);return [s.fontSize,s.lineHeight,s.letterSpacing,s.fontWeight];
                }""")
                self.assertEqual(actual, shared)
                bounds = self.page.evaluate("""()=>{
                    const d=document.querySelector('#model-dialog'),h=document.querySelector('#dialog-title'),
                        c=document.querySelector('.dialog-close');
                    return {dialog:d.getBoundingClientRect().toJSON(),title:h.getBoundingClientRect().toJSON(),
                        close:c.getBoundingClientRect().toJSON(),innerWidth:d.clientWidth,scrollWidth:d.scrollWidth};
                }""")
                self.assertLessEqual(bounds['scrollWidth'], bounds['innerWidth'])
                self.assertGreaterEqual(bounds['dialog']['left'], 0)
                self.assertLessEqual(bounds['dialog']['right'], width)
                self.assertLessEqual(bounds['title']['right'], bounds['dialog']['right'])
                self.assertGreaterEqual(bounds['title']['top'], bounds['close']['bottom'])
                self.assertLessEqual(bounds['close']['right'], bounds['dialog']['right'])
                self.assertTrue(self.page.locator('.dialog-close').is_visible())
                self.page.keyboard.press('Escape')
                self.assertFalse(dialog.is_visible())

    def test_metadata_columns_preserve_budget_unknown_zero_and_accessible_safe_links(self):
        payload = copy.deepcopy(self.payload)
        payload['submissions'] = payload['submissions'][:3]
        payload['config']['default_filters']['only_models_with_code'] = False
        for index, submission in enumerate(payload['submissions']):
            submission.update(schema_version='2.0', data_release='v1', evaluator_ref='fixture-evaluator')
            submission.pop('hparam_trials', None)
            submission.pop('num_runs', None)
            for result in submission['results']:
                result['num_runs'] = 3
                result.pop('hparam_trials', None)
                if index == 0:
                    result['hparam_trials'] = 11 if result['dataset'] == 'pokec-regions' else 5
                elif index == 2:
                    result['hparam_trials'] = 0
        payload['submissions'][1]['paper_url'] = None
        payload['submissions'][1]['training_code_url'] = 'javascript:alert(1)'
        payload['submissions'][2]['method_type'] = 'in_context'
        payload['submissions'][2]['paper_url'] = 'http://example.test/insecure-paper'
        payload['submissions'][2]['code_availability'] = 'unavailable'
        self.with_payload(payload)
        self.open()
        headers = self.page.locator('#leaderboard-head th[data-metadata-column]')
        self.assertEqual(headers.evaluate_all('(cells) => cells.map(el => el.dataset.metadataColumn)'), ['date', 'hpo', 'paper', 'code'])
        budgets = []
        for index, expected_budget in enumerate(('Varies', '—', '0')):
            name = payload['submissions'][index]['model_name']
            row = self.page.locator('#leaderboard-body tr').filter(has_text=name)
            self.assertEqual(row.count(), 1)
            hpo = row.locator('[data-metadata-column="hpo"]')
            self.assertEqual(hpo.inner_text(), expected_budget)
            if index == 1:
                self.assertIn('unknown', hpo.get_attribute('aria-label'))
            time = row.locator('[data-metadata-column="date"] time')
            self.assertEqual(time.get_attribute('datetime'), payload['submissions'][index]['submitted_at'])
            for kind in ('paper', 'code'):
                cell = row.locator(f'[data-metadata-column="{kind}"]')
                if index == 0:
                    link = cell.locator('a')
                    expected_url = payload['submissions'][index]['paper_url' if kind == 'paper' else 'training_code_url']
                    self.assertEqual(link.get_attribute('href'), expected_url)
                    self.assertEqual(link.get_attribute('target'), '_blank')
                    self.assertEqual(set(link.get_attribute('rel').split()), {'noopener', 'noreferrer'})
                    label = link.get_attribute('aria-label')
                    self.assertIn(f'{kind.title()} for {name}', label)
                    self.assertIn('opens in a new tab', label)
                else:
                    self.assertEqual(cell.locator('a').count(), 0)
                    self.assertIn(f'{kind.title()} unavailable for {name}', cell.locator('[role="img"]').get_attribute('aria-label'))
            budgets.append({'model': name, 'budget': hpo.inner_text(), 'details': hpo.get_attribute('aria-label')})
        self.page.set_viewport_size({'width': 375, 'height': 900})
        self.page.wait_for_function("[...document.querySelectorAll('.metadata-column')].every(el => getComputedStyle(el).display === 'none')")
        self.page.locator('#leaderboard-body .model-button').first.click()
        self.assertTrue(self.page.locator('#model-dialog').is_visible())
        self.assertIn('Varies', self.page.locator('#dialog-content').inner_text())
        self.brand_observations.append({'check': 'metadata_budget_and_links', 'budgets': budgets,
                                        'mobile_metadata_hidden_details_available': True})

    def test_best_reported_means_include_negative_ties_and_recompute_after_filtering(self):
        payload = copy.deepcopy(self.payload)
        payload['config']['default_filters']['only_models_with_code'] = False
        means = (-.8, -.2, -.2, -.20001)
        for submission, mean in zip(payload['submissions'], means):
            submission['results'] = [{'setting': setting, 'dataset': 'hm-prices', 'value': mean,
                                      'std': .01, 'num_runs': 3} for setting in ('RL', 'TH')]
        payload['submissions'][0]['results'].append({'setting': 'TH', 'dataset': 'city-roads-M', 'value': 999, 'num_runs': 3})
        self.with_payload(payload)
        self.open('?task=node_regression&setting=TH')
        expected_best = {submission['model_name'] for submission in payload['submissions'][1:3]}
        highlighted = set()
        for submission in payload['submissions']:
            row = self.page.locator('#leaderboard-body tr').filter(has_text=submission['model_name'])
            cell = row.locator('td[data-state="value"]').first
            best = submission['model_name'] in expected_best
            self.assertEqual(cell.get_attribute('data-best'), str(best).lower())
            weight = cell.evaluate('(el) => Number(getComputedStyle(el).fontWeight)')
            self.assertEqual(weight, 700 if best else 400)
            if best:
                self.assertIn('Highest reported mean in this view', cell.get_attribute('aria-label'))
                highlighted.add(submission['model_name'])
        self.assertEqual(highlighted, expected_best)
        self.assertEqual(self.page.locator('[data-state="missing"].metric-best, [data-state="unavailable"].metric-best').count(), 0)
        self.page.locator('#model-search').fill(payload['submissions'][0]['model_name'])
        self.assertEqual(self.page.locator('#leaderboard-body tr').count(), 1)
        self.assertEqual(self.page.locator('#leaderboard-body td[data-state="value"]').first.get_attribute('data-best'), 'true')
        self.page.locator('#model-search').fill('')
        self.assertEqual(self.page.locator('#leaderboard-body .metric-best').count(), 2)
        self.brand_observations.append({'check': 'negative_r2_best_means', 'raw_means': means,
                                        'tied_best_models': sorted(expected_best), 'refilter_recomputes_best': True,
                                        'missing_and_unavailable_excluded': True})

    def test_all_datasets_keep_individual_metrics_sorting_history_and_four_settings(self):
        self.open('?setting=RL&task=all')
        self.assertEqual(self.page.locator('#task-tab-all').get_attribute('aria-selected'), 'true')
        self.assertIn('Dataset-specific metrics', self.page.locator('#metric-note').inner_text())
        keys = self.page.locator('#leaderboard-head [data-sort-key]').evaluate_all('(buttons) => buttons.map(el => el.dataset.sortKey)')
        self.assertEqual(keys, ['model'] + [dataset['id'] for dataset in self.payload['datasets']])
        self.assertEqual(len(keys) - 1, 14)
        tasks = {task['id']: task for task in self.payload['config']['task_families']}
        for dataset in self.payload['datasets']:
            self.assertIn(tasks[dataset['task']]['metric_label'],
                          self.page.locator(f'[data-sort-key="{dataset["id"]}"]').inner_text())
            name = f"{dataset['display_name']} {tasks[dataset['task']]['metric_label']}: sort descending"
            self.assertEqual(self.page.get_by_role('button', name=name, exact=True).count(), 1)
        self.assertEqual(self.page.get_by_role('button', name='Model Name / variant: sort descending', exact=True).count(), 1)
        setting_tabs = self.page.get_by_role('tablist', name='Experimental setting', exact=True)
        self.assertEqual(setting_tabs.get_by_role('tab').all_text_contents(), ['RL', 'RH', 'TH', 'THI'])
        hidden_label = self.page.locator('#setting-tabs-label').bounding_box()
        self.assertLessEqual(hidden_label['width'], 1)
        self.assertLessEqual(hidden_label['height'], 1)
        self.page.locator('[data-sort-key="city-reviews"]').click()
        expected = sorted(self.payload['submissions'], key=lambda submission: (
            -next((result['value'] for result in submission['results'] if result['dataset'] == 'city-reviews' and result['setting'] == 'RL'), -float('inf')),
            submission['model_name']))
        self.assertEqual(self.page.locator('#leaderboard-body .model-button strong').all_text_contents(),
                         [submission['model_name'] for submission in expected])
        self.page.locator('#task-tab-node_regression').click()
        self.assertEqual(self.page.locator('#task-tab-node_regression').get_attribute('aria-selected'), 'true')
        self.assertEqual(self.page.locator('#leaderboard-head [data-sort-key]').count() - 1, 7)
        self.page.go_back()
        self.page.wait_for_function("document.getElementById('task-tab-all').getAttribute('aria-selected') === 'true'")
        self.assertEqual(self.page.locator('[data-sort-key="city-reviews"]').locator('..').get_attribute('aria-sort'), 'descending')
        query = self.page.evaluate('Object.fromEntries(new URL(location.href).searchParams)')
        self.assertEqual(query['task'], 'all')
        self.assertEqual(query['sort'], 'city-reviews')
        self.page.go_forward()
        self.page.wait_for_function("document.getElementById('task-tab-node_regression').getAttribute('aria-selected') === 'true'")
        self.page.locator('#task-tab-all').click()
        self.assertEqual(self.page.locator('#leaderboard-head [data-sort-key]').count() - 1, 14)
        self.brand_observations.append({'check': 'all_datasets_individual_metrics', 'dataset_columns': 14,
                                        'settings': ['RL', 'RH', 'TH', 'THI'], 'sort_dataset': 'city-reviews',
                                        'history_preserved': True, 'separate_view_return': True,
                                        'visible_setting_label_hidden_accessible_name_retained': True})

    def test_print_records_preserve_order_labels_values_and_best_emphasis(self):
        self.open('?task=all')
        for setting in ('RL', 'RH', 'TH', 'THI'):
            self.page.get_by_role('tab', name=setting, exact=True).click()
            self.page.locator('#leaderboard-head [data-sort-key]').first.click()
            expected = self.page.evaluate('''() => {
                const table = document.querySelector('#leaderboard-table');
                const labels = [...table.tHead.rows[0].cells].slice(1).map(cell =>
                    (cell.querySelector('.sort-label') || cell).textContent.trim().replace(/\\s+/g, ' '));
                return [...table.tBodies[0].rows].map(row => ({
                    name: row.cells[0].querySelector('.model-button').textContent,
                    fields: [...row.cells].slice(1).map((cell, i) => ({
                        label: labels[i], text: cell.textContent, html: cell.innerHTML,
                        best: cell.classList.contains('metric-best')
                    }))
                }));
            }''')
            self.page.evaluate("dispatchEvent(new Event('beforeprint'))")
            self.assertFalse(self.page.locator('.print-tables').is_visible())
            actual = self.page.evaluate('''() => [...document.querySelectorAll('.print-record')].map(table => ({
                name: table.querySelector('.print-model-name').textContent,
                fields: [...table.tBodies[0].rows].map(row => ({
                    label: row.cells[0].textContent, text: row.cells[1].textContent,
                    html: row.cells[1].innerHTML, best: row.cells[1].classList.contains('metric-best')
                }))
            }))''')
            self.assertEqual(actual, expected)
            self.page.emulate_media(media='print')
            self.assertTrue(self.page.locator('.print-tables').is_visible())
            self.assertFalse(self.page.locator('#leaderboard-table').is_visible())
            for value in self.page.locator('.print-record .metric-value').all():
                expected_weight = '700' if 'metric-best' in value.get_attribute('class') else '400'
                self.assertEqual(value.evaluate('(e)=>getComputedStyle(e).fontWeight'), expected_weight)
            self.page.emulate_media(media='screen')
            self.page.evaluate("dispatchEvent(new Event('afterprint'))")
            self.assertEqual(self.page.locator('.print-record').count(), 0)
            self.assertTrue(self.page.locator('#leaderboard-table').is_visible())

    def test_print_lifecycle_reconciles_stale_dialog_and_preserves_original_focus(self):
        self.open('?task=all')
        self.page.locator('.model-button').first.click()
        self.page.locator('.dialog-close').focus()
        before = self.page.evaluate('''() => {
            window.originalPrintFocus = document.activeElement;
            return {url: location.href, history: history.length,
                    title: document.querySelector('#dialog-title').textContent,
                    content: document.querySelector('#dialog-content').innerHTML};
        }''')
        for _ in range(2):
            self.page.evaluate("dispatchEvent(new Event('beforeprint'))")
            self.assertEqual(self.page.locator('.print-model-details').count(), 1)
            self.assertEqual(self.page.locator('.print-model-details h1').text_content(), before['title'])
            self.assertEqual(self.page.locator('.print-model-details [id], .print-model-details [tabindex]').count(), 0)
            self.assertFalse(self.page.locator('.print-model-details').is_visible())
            self.assertTrue(self.page.evaluate('document.activeElement===window.originalPrintFocus'))
        # A real PDF invokes the browser's print lifecycle, including cleanup.
        pdf = self.page.pdf(format='A4')
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertEqual(self.page.locator('.print-model-details').count(), 0)
        self.assertTrue(self.page.locator('#model-dialog').evaluate('(e)=>e.open && e.matches(":modal")'))
        self.assertTrue(self.page.evaluate('document.activeElement===window.originalPrintFocus'))
        self.assertEqual(self.page.url, before['url'])
        self.assertEqual(self.page.evaluate('history.length'), before['history'])
        self.assertEqual(self.page.locator('#dialog-content').inner_html(), before['content'])
        self.page.evaluate("dispatchEvent(new Event('beforeprint'))")
        self.page.keyboard.press('Escape')
        # No afterprint: a normal print must discard the stale model snapshot.
        self.page.evaluate("dispatchEvent(new Event('beforeprint'))")
        self.assertEqual(self.page.locator('.print-model-details').count(), 0)
        self.assertGreater(self.page.locator('.print-record').count(), 0)
        self.assertFalse(self.page.evaluate("document.body.classList.contains('printing-model')"))
        self.page.evaluate("dispatchEvent(new Event('afterprint'))")
        self.assertEqual(self.page.locator('.print-record').count(), 0)

    def test_catalog_print_labels_are_complete_without_javascript(self):
        context = self.browser.new_context(java_script_enabled=False, viewport={'width':794,'height':1123})
        try:
            page = context.new_page()
            page.goto(self.base+'index.html')
            labels = page.locator('.dataset-catalog thead th').all_text_contents()[1:]
            self.assertEqual(page.locator('.dataset-catalog tbody tr').count(), 14)
            for row in page.locator('.dataset-catalog tbody tr').all():
                self.assertEqual([cell.get_attribute('data-print-label') for cell in row.locator('td').all()], labels)
            page.emulate_media(media='print')
            bounds = page.locator('.dataset-catalog').bounding_box()
            self.assertLessEqual(bounds['x']+bounds['width'], 795)
            self.assertTrue(page.locator('.dataset-catalog tbody tr').last.is_visible())
        finally:
            context.close()

    def test_print_handles_error_and_empty_views_without_stale_records(self):
        self.page.route('**/data/leaderboard.json', lambda route: route.fulfill(status=503, body='unavailable'))
        self.page.goto(self.base+'leaderboard.html')
        self.page.wait_for_selector('#load-error', state='visible')
        self.page.evaluate("dispatchEvent(new Event('beforeprint'))")
        self.assertEqual(self.page.locator('.print-record').count(), 0)
        self.page.emulate_media(media='print')
        self.assertTrue(self.page.locator('#load-error').is_visible())
        self.assertTrue(self.page.locator('#load-error a[href="leaderboard.csv"]').is_visible())
        self.page.emulate_media(media='screen')
        self.page.evaluate("dispatchEvent(new Event('afterprint'))")
        self.page.unroute('**/data/leaderboard.json')
        self.open('?task=all')
        self.page.evaluate("dispatchEvent(new Event('beforeprint'))")
        self.assertGreater(self.page.locator('.print-record').count(), 0)
        self.page.locator('#model-search').fill('no matching model')
        self.page.evaluate("dispatchEvent(new Event('beforeprint'))")
        self.assertEqual(self.page.locator('.print-record').count(), 0)
        self.page.emulate_media(media='print')
        self.assertTrue(self.page.locator('#empty-state').is_visible())
        self.page.emulate_media(media='screen')
        self.page.evaluate("dispatchEvent(new Event('afterprint'))")

    def test_print_long_model_text_wraps_without_scaling_the_page(self):
        payload = copy.deepcopy(self.payload)
        payload['submissions'][0]['model_name'] = 'M' * 120
        payload['submissions'][0]['model_variant'] = 'V' * 120
        payload['submissions'][0]['notes'] = 'Long notes. ' * 300
        self.with_payload(payload)
        self.open('?task=all&submission=' + payload['submissions'][0]['id'])
        self.page.set_viewport_size({'width':794,'height':1123})
        self.page.evaluate("dispatchEvent(new Event('beforeprint'))")
        self.page.emulate_media(media='print')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 794)
        self.assertEqual(self.page.locator('.print-model-details h1').inner_text(), 'M' * 120)
        self.assertEqual(self.page.locator('.print-model-details .dialog-lead').inner_text(), 'V' * 120)
        self.assertEqual(self.page.locator('.print-prose-field dd').text_content(), payload['submissions'][0]['notes'])
        self.assertEqual(self.page.locator('.print-model-details h1').evaluate('(e)=>getComputedStyle(e).fontSize'), '32px')
        self.page.emulate_media(media='screen')
        self.page.evaluate("dispatchEvent(new Event('afterprint'))")

    def test_model_details_identify_each_result_metric(self):
        self.open('?task=all')
        self.page.locator('.model-button').first.click()
        labels = {task['id']: task['metric_label'] for task in self.payload['config']['task_families']}
        datasets = {dataset['display_name']: labels[dataset['task']] for dataset in self.payload['datasets']}
        results = self.page.locator('#dialog-content .detail-list').last.locator('div')
        self.assertGreater(results.count(), 0)
        for result in results.all():
            self.assertTrue(result.locator('dd').inner_text().startswith(datasets[result.locator('dt').inner_text()]+': '))

    def test_unavailable_sort_headers_explain_the_setting_without_action_arrows(self):
        self.open('?setting=TH&task=all')
        unavailable = self.page.locator('.sort-button:disabled')
        self.assertEqual(unavailable.count(), 4)
        before = self.page.url
        for button in unavailable.all():
            self.assertIn(': unavailable in TH', button.get_attribute('aria-label'))
            self.assertNotIn(': sort ', button.get_attribute('aria-label'))
            self.assertEqual(button.locator('.sort-indicator').evaluate('(e)=>getComputedStyle(e).visibility'), 'hidden')
            self.assertEqual(button.evaluate('(e)=>e.closest("th").title'), 'This dataset does not support TH.')
            button.evaluate('(e)=>e.click()')
            self.assertEqual(self.page.url, before)
        self.page.get_by_role('tab', name='RH', exact=True).click()
        self.assertEqual(self.page.locator('.sort-button:disabled').count(), 0)
        button = self.page.locator('[data-sort-key="city-reviews"]')
        self.assertIn(': sort ', button.get_attribute('aria-label'))
        self.assertEqual(button.locator('.sort-indicator').evaluate('(e)=>getComputedStyle(e).visibility'), 'visible')
        self.assertEqual(button.evaluate('(e)=>e.closest("th").title'), '')

    def test_single_submission_summary_uses_singular_total(self):
        payload = copy.deepcopy(self.payload)
        payload['submissions'] = payload['submissions'][:1]
        self.with_payload(payload)
        self.open('?task=all')
        self.assertIn('1 model with results', self.page.locator('#result-summary').inner_text())
        self.assertTrue(self.page.locator('#result-summary').inner_text().endswith('1 submission overall'))

    def test_static_fallback_has_downloads_without_javascript_or_app_script(self):
        for javascript in (False, True):
            with self.subTest(javascript=javascript):
                page = self.browser.new_page(java_script_enabled=javascript, viewport={'width':375,'height':800}, reduced_motion='reduce')
                try:
                    if javascript: page.route('**/assets/app.js', lambda route: route.abort())
                    page.goto(self.base+'leaderboard.html')
                    self.assertTrue(page.locator('#leaderboard-fallback').is_visible())
                    self.assertFalse(page.locator('#leaderboard-panel').is_visible())
                    self.assertFalse(page.locator('#leaderboard-controls').is_visible())
                    self.assertEqual(page.locator('#view-results-link').get_attribute('href'), '#leaderboard')
                    page.locator('#view-results-link').click()
                    self.assertTrue(page.url.endswith('#leaderboard'))
                    for filename in ('leaderboard.csv','leaderboard.xlsx'):
                        link = page.locator('#leaderboard-fallback a[href="'+filename+'"]')
                        self.assertTrue(link.is_visible())
                        with page.expect_download() as info: link.click()
                        self.assertEqual(info.value.suggested_filename, filename)
                        self.assertIsNone(info.value.failure())
                    page.emulate_media(media='print')
                    self.assertTrue(page.locator('#leaderboard-fallback').is_visible())
                    self.assertFalse(page.locator('#result-summary').is_visible())
                finally:
                    page.close()

    def test_delayed_app_preserves_focus_when_replacing_static_downloads(self):
        for target in ('#leaderboard-fallback a[href="leaderboard.csv"]', '.project-navigation a[href="datasets.html"]'):
            with self.subTest(target=target):
                page = self.browser.new_page(reduced_motion='reduce')
                pending = []
                try:
                    page.route('**/assets/app.js', lambda route: pending.append(route))
                    page.goto(self.base+'leaderboard.html', wait_until='commit')
                    page.wait_for_selector('#leaderboard-fallback', state='visible')
                    page.locator(target).focus()
                    page.wait_for_function('document.readyState!=="loading"')
                    self.assertEqual(len(pending), 1)
                    pending[0].fulfill(body=(ROOT/'site/assets/app.js').read_text(), content_type='text/javascript')
                    page.wait_for_function("document.querySelector('#leaderboard-panel').getAttribute('aria-busy')==='false'")
                    self.assertFalse(page.locator('#leaderboard-fallback').is_visible())
                    self.assertTrue(page.locator('#leaderboard-panel').is_visible())
                    self.assertEqual(page.locator('#view-results-link').get_attribute('href'), '#leaderboard-controls')
                    if target.startswith('#leaderboard-fallback'):
                        self.assertEqual(page.evaluate('document.activeElement.id'), 'leaderboard-panel')
                    else:
                        self.assertTrue(page.locator(target).evaluate('(e)=>e===document.activeElement'))
                finally:
                    page.close()


if __name__ == '__main__': unittest.main()
