(() => {
  "use strict";

  // Keep bookmarked single-page leaderboard URLs working after the page split.
  // Section navigation itself uses ordinary links and browser history.
  function redirectLegacyLeaderboardUrl() {
    if (document.body?.dataset.page !== "home") return;

    const query = new URLSearchParams(window.location.search);
    const leaderboardKeys = ["setting", "task", "q", "code", "sort", "order", "submission"];
    const leaderboardHashes = ["#leaderboard", "#leaderboard-controls", "#protocol", "#submit", "#model-search"];
    if (!leaderboardKeys.some((key) => query.has(key))
        && !leaderboardHashes.includes(window.location.hash)) return;

    const target = new URL("leaderboard.html", window.location.href);
    target.search = window.location.search;
    target.hash = window.location.hash;
    window.location.replace(target.href);
  }

  function revealCurrentSection() {
    const navigation = document.querySelector('.project-navigation');
    const current = navigation?.querySelector('[aria-current="page"]');
    if (!navigation || !current) return;
    const container = navigation.getBoundingClientRect();
    const item = current.getBoundingClientRect();
    const padding = parseFloat(getComputedStyle(navigation).paddingLeft) || 0;
    if (item.right > container.right - padding) {
      navigation.scrollLeft += item.right - container.right + padding;
    } else if (item.left < container.left + padding) {
      navigation.scrollLeft -= container.left + padding - item.left;
    }
  }

  function initializeCatalogScrollHint() {
    const viewport = document.querySelector('.catalog-scroll');
    const hint = document.getElementById('catalog-scroll-hint');
    if (!viewport || !hint) return;
    function update() {
      const remaining = viewport.scrollWidth - viewport.clientWidth - viewport.scrollLeft;
      hint.hidden = remaining <= 2;
      const descriptions = new Set((viewport.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
      if (hint.hidden) descriptions.delete(hint.id);
      else descriptions.add(hint.id);
      if (descriptions.size) viewport.setAttribute('aria-describedby', [...descriptions].join(' '));
      else viewport.removeAttribute('aria-describedby');
    }
    viewport.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update, { passive: true });
    if (window.ResizeObserver) {
      const observer = new ResizeObserver(update);
      observer.observe(viewport);
      observer.observe(viewport.querySelector('table'));
    }
    if (document.fonts) document.fonts.ready.then(update);
    update();
  }

  function initializeHorizontalKeyboardScroll() {
    // WebKit does not natively scroll focused overflow regions with arrow keys.
    // Keep descendant controls and text-selection/browser shortcuts untouched.
    document.querySelectorAll('.catalog-scroll, .table-scroll, .code-block').forEach((viewport) => {
      viewport.addEventListener('keydown', (event) => {
        if (event.target !== viewport || event.defaultPrevented || event.isComposing
            || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
            || !['ArrowLeft', 'ArrowRight'].includes(event.key)
            || viewport.scrollWidth <= viewport.clientWidth + 1) return;
        event.preventDefault();
        viewport.scrollLeft += event.key === 'ArrowRight' ? 40 : -40;
      });
    });
  }

  function initializeTableFocusScroll() {
    const viewport = document.querySelector('.table-scroll');
    if (!viewport) return;
    viewport.addEventListener('focusin', (event) => {
      const target = event.target;
      const cell = target.closest('th, td');
      if (!cell || !viewport.contains(cell)) return;
      // Native keyboard focus may leave a control behind the sticky column.
      // Do not move pointer-focused controls between pointerdown and click.
      window.requestAnimationFrame(() => {
        if (document.activeElement !== target || !target.matches(':focus-visible')) return;
        const bounds = viewport.getBoundingClientRect();
        const firstCell = cell.parentElement.cells[0];
        const left = cell === firstCell
          ? bounds.left + viewport.clientLeft
          : Math.max(bounds.left + viewport.clientLeft, firstCell.getBoundingClientRect().right);
        const right = bounds.left + viewport.clientLeft + viewport.clientWidth;
        const control = target.getBoundingClientRect();
        const gap = 6;
        if (control.left < left + gap) viewport.scrollLeft += control.left - left - gap;
        else if (control.right > right - gap) viewport.scrollLeft += control.right - right + gap;
      });
    });
  }

  function initializeNavigation() {
    redirectLegacyLeaderboardUrl();
    initializeCatalogScrollHint();
    initializeHorizontalKeyboardScroll();
    initializeTableFocusScroll();
    initializeCodeCopy();
    window.requestAnimationFrame(revealCurrentSection);
    if (document.fonts) document.fonts.ready.then(revealCurrentSection);
    window.addEventListener('resize', revealCurrentSection, { passive: true });
  }

  function initializeCodeCopy() {
    // Keep the original selectable examples when the Clipboard API is absent.
    if (!navigator.clipboard?.writeText) return;
    document.querySelectorAll('.code-block').forEach((block) => {
      const code = block.querySelector('code');
      if (!code) return;
      const citation = code.classList.contains('language-bibtex');
      const heading = block.closest('section')?.querySelector('h2')?.textContent;
      const wrapper = document.createElement('div');
      wrapper.className = 'code-example';
      const toolbar = document.createElement('div');
      toolbar.className = 'code-toolbar';
      const status = document.createElement('span');
      status.className = 'copy-status';
      status.setAttribute('role', 'status');
      status.setAttribute('aria-live', 'polite');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'copy-code';
      button.textContent = citation ? 'Copy citation' : 'Copy code';
      button.setAttribute('aria-label', `${button.textContent}${heading ? ': ' + heading : ''}`);
      toolbar.append(status, button);
      block.before(wrapper);
      wrapper.append(toolbar, block);
      let pending = false;
      button.addEventListener('click', async () => {
        if (pending) return;
        pending = true;
        // aria-disabled keeps keyboard focus while the permission request runs.
        button.setAttribute('aria-disabled', 'true');
        status.textContent = 'Copying…';
        try {
          await navigator.clipboard.writeText(code.textContent);
          status.textContent = 'Copied';
        } catch (_) {
          status.textContent = 'Could not copy. Select the text and copy it manually.';
        } finally {
          pending = false;
          button.removeAttribute('aria-disabled');
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeNavigation, { once: true });
  } else {
    initializeNavigation();
  }
})();
