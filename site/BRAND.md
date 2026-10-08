# GraphLand visual reference

The leaderboard follows the shared visual system of [Yandex Research](https://research.yandex.com/). Its white scientific introduction and line illustration follow [Publications](https://research.yandex.com/publications); the same header, fonts and responsive container were checked against [About](https://research.yandex.com/about). These are measurements of the public site captured on 2026-10-08, rather than a claim of official design-system certification.

| Viewport | Content width / horizontal gutters | Fixed header | Two-part logo | H1 |
| --- | --- | --- | --- | --- |
| 1601px and wider | 1360px maximum / 30px | 63px | 83×31 + 99×31 | 48px |
| 1000–1600px | 928px maximum / 20px | 49px | 59×22 + 70×22 | 40px |
| 768–999px | 696px maximum / 20px | 49px | 59×22 + 70×22 | 40px |
| 767px and narrower | viewport minus 32px / 16px | 53px | 75×28 + 89×28 | 30px |

Interface text and numeric tables use YS Text; descriptive editorial text uses Merriweather Light. Heading weight is 500, regular interface text is 400, serif text is 300. H1 tracking is −0.008em on desktop and −0.004em on mobile. Content H2 uses 32px / 24px / 18px across the wide, desktop and mobile scales. Hero text widths follow the reference's 690px / 400px / 300px desktop steps, then fill the mobile content area. Buttons have a 30px radius, with 52px / 18px type above 1600px and approximately 40px / 14px type below. The Submit accent is #fed42b, with #f5c400 on hover.

The five WOFF2 files and three SVG drawings are hosted locally under `assets/brand/`. Their source URLs, captured build and SHA-256 hashes are recorded in [provenance.json](assets/brand/provenance.json). HTML and CSS use relative URLs, including when served under `/graphland/`. Three critical font faces are preloaded; the remaining faces load on demand. Font declarations are paired with actual files for every used weight.

Content is specific to GraphLand: the comparison table, protocol explanations, filters and review disclosures retain their scientific meaning. The search icon leads to the model search field. The mobile menu keeps a 44px hit area around the reference-sized glyph. Search placeholders and footer hover retain readable contrast; the reference's low-contrast placeholder and gray-on-black hover were not copied.

Run `python -m unittest discover -s tests/leaderboard -v` for build/data/frontend checks and `python -m unittest discover -s tests/browser -v` with the pinned Playwright requirements and Chromium installed for interaction/brand checks. Brand tests verify served font bytes, actual glyph fonts, SVG geometry, responsive boundaries and header search. A deliberate font 404 establishes that the glyph test detects fallback rather than merely reading the CSS font-family. Dialog history includes a deterministic delayed-close focus regression.
