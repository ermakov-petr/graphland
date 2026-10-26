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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", redirectLegacyLeaderboardUrl, { once: true });
  } else {
    redirectLegacyLeaderboardUrl();
  }
})();
