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

  function initializeNavigation() {
    redirectLegacyLeaderboardUrl();
    window.requestAnimationFrame(revealCurrentSection);
    if (document.fonts) document.fonts.ready.then(revealCurrentSection);
    window.addEventListener('resize', revealCurrentSection, { passive: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeNavigation, { once: true });
  } else {
    initializeNavigation();
  }
})();
