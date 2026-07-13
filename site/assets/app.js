(() => {
  "use strict";

  const SETTING_IDS = ["RL", "RH", "TH", "THI"];
  const TASK_IDS = [
    "multiclass_node_classification",
    "binary_node_classification",
    "node_regression",
  ];

  const state = {
    payload: null,
    datasetsById: new Map(),
    settingsById: new Map(),
    tasksById: new Map(),
    setting: "RL",
    task: "multiclass_node_classification",
    search: "",
    codeOnly: false,
    sortKey: "model",
    sortDirection: "asc",
    dialogTrigger: null,
  };

  const elements = {};

  function cacheElements() {
    elements.header = document.querySelector("[data-site-header]");
    elements.menuButton = document.querySelector(".menu-button");
    elements.navigation = document.getElementById("primary-navigation");
    elements.settingTabs = document.getElementById("setting-tabs");
    elements.taskTabs = document.getElementById("task-tabs");
    elements.settingDescription = document.getElementById("setting-description");
    elements.search = document.getElementById("model-search");
    elements.codeFilter = document.getElementById("code-filter");
    elements.panel = document.getElementById("leaderboard-panel");
    elements.resultSummary = document.getElementById("result-summary");
    elements.metricNote = document.getElementById("metric-note");
    elements.tableScroll = document.querySelector(".table-scroll");
    elements.table = document.getElementById("leaderboard-table");
    elements.tableCaption = elements.table.querySelector("caption");
    elements.tableHead = document.getElementById("leaderboard-head");
    elements.tableBody = document.getElementById("leaderboard-body");
    elements.emptyState = document.getElementById("empty-state");
    elements.emptyTitle = elements.emptyState.querySelector("h3");
    elements.emptyCopy = elements.emptyState.querySelector("p");
    elements.emptyLink = elements.emptyState.querySelector("a");
    elements.loadError = document.getElementById("load-error");
    elements.demoNotice = document.getElementById("demo-data-notice");
    elements.dialog = document.getElementById("model-dialog");
    elements.dialogTitle = document.getElementById("dialog-title");
    elements.dialogContent = document.getElementById("dialog-content");
    elements.dialogClose = document.querySelector("[data-dialog-close]");
  }

  function createElement(tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    if (text !== undefined && text !== null) {
      element.textContent = String(text);
    }
    return element;
  }

  function safeExternalUrl(value) {
    if (typeof value !== "string") {
      return null;
    }
    try {
      const parsed = new URL(value);
      if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
        return null;
      }
      return parsed.href;
    } catch (_error) {
      return null;
    }
  }

  function isSettingAvailable(dataset, setting) {
    return Boolean(
      dataset
      && Array.isArray(dataset.available_settings)
      && dataset.available_settings.includes(setting),
    );
  }

  function getResult(submission, setting, datasetId) {
    if (!submission || !Array.isArray(submission.results)) {
      return null;
    }
    return submission.results.find(
      (result) => result.setting === setting && result.dataset === datasetId,
    ) || null;
  }

  function formatMetric(result, dataset) {
    if (!result || !dataset || !Number.isFinite(result.value)) {
      return "—";
    }

    const display = dataset.display || {};
    const decimals = Number.isInteger(display.decimals) ? display.decimals : 3;
    const multiplier = display.style === "percentage" ? 100 : 1;
    const suffix = display.style === "percentage" ? "%" : "";
    const value = (result.value * multiplier).toFixed(decimals);

    if (Number.isFinite(result.std)) {
      const std = (result.std * multiplier).toFixed(decimals);
      return `${value}${suffix} ± ${std}${suffix}`;
    }
    return `${value}${suffix}`;
  }

  function submissionName(submission) {
    return `${submission.model_name || ""} ${submission.model_variant || ""}`.trim();
  }

  function isDemoSubmission(submission) {
    return Boolean(
      submission
      && typeof submission.id === "string"
      && submission.id.startsWith("demo-"),
    );
  }

  function hasDemoSubmissions(payload) {
    return Boolean(
      payload
      && Array.isArray(payload.submissions)
      && payload.submissions.some(isDemoSubmission),
    );
  }

  function compareModelNames(left, right) {
    return submissionName(left).localeCompare(submissionName(right), "en", {
      numeric: true,
      sensitivity: "base",
    });
  }

  function sortableValue(submission, datasetId, setting, datasetsById) {
    const dataset = datasetsById instanceof Map
      ? datasetsById.get(datasetId)
      : datasetsById && datasetsById[datasetId];

    if (!dataset || !isSettingAvailable(dataset, setting)) {
      return { missing: true, value: null };
    }
    const result = getResult(submission, setting, datasetId);
    if (!result || !Number.isFinite(result.value)) {
      return { missing: true, value: null };
    }
    return { missing: false, value: result.value };
  }

  function compareRows(left, right, options = {}) {
    const key = options.key || "model";
    const direction = options.direction === "desc" ? "desc" : "asc";
    const setting = options.setting || state.setting;
    const datasetsById = options.datasetsById || state.datasetsById;

    if (key === "model") {
      const modelComparison = compareModelNames(left, right);
      return direction === "desc" ? -modelComparison : modelComparison;
    }

    const leftValue = sortableValue(left, key, setting, datasetsById);
    const rightValue = sortableValue(right, key, setting, datasetsById);

    // Missing and unavailable cells always sort after numeric values, regardless of direction.
    if (leftValue.missing !== rightValue.missing) {
      return leftValue.missing ? 1 : -1;
    }
    if (!leftValue.missing && leftValue.value !== rightValue.value) {
      const numericComparison = leftValue.value - rightValue.value;
      return direction === "desc" ? -numericComparison : numericComparison;
    }
    return compareModelNames(left, right);
  }

  function readQueryState() {
    const params = new URLSearchParams(window.location.search);
    const setting = params.get("setting");
    const task = params.get("task");
    if (SETTING_IDS.includes(setting)) {
      state.setting = setting;
    }
    if (TASK_IDS.includes(task)) {
      state.task = task;
    }
  }

  function writeQueryState() {
    const url = new URL(window.location.href);
    url.searchParams.set("setting", state.setting);
    url.searchParams.set("task", state.task);
    window.history.replaceState(null, "", url);
  }

  function setActiveTabs() {
    document.querySelectorAll("[data-setting]").forEach((button) => {
      const selected = button.dataset.setting === state.setting;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    document.querySelectorAll("[data-task]").forEach((button) => {
      const selected = button.dataset.task === state.task;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    elements.panel.setAttribute(
      "aria-labelledby",
      `setting-tab-${state.setting} task-tab-${state.task}`,
    );
  }

  function updateSettingDescription() {
    const setting = state.settingsById.get(state.setting);
    elements.settingDescription.replaceChildren();
    if (!setting) {
      return;
    }
    const access = setting.information_access === "inductive" ? "Inductive" : "Transductive";
    const name = createElement("span", "setting-name", `${setting.name} · ${access}`);
    const description = createElement("span", null, setting.description);
    elements.settingDescription.append(name, description);
  }

  function updateMetricNote() {
    const task = state.tasksById.get(state.task);
    if (!task) {
      return;
    }
    elements.metricNote.textContent = `Canonical metric: ${task.metric_label} · higher is better`;
  }

  function taskDatasets() {
    if (!state.payload) {
      return [];
    }
    return state.payload.datasets.filter((dataset) => dataset.task === state.task);
  }

  function filteredSubmissions() {
    if (!state.payload) {
      return [];
    }
    const query = state.search.trim().toLocaleLowerCase("en");
    return state.payload.submissions.filter((submission) => {
      if (state.codeOnly && submission.code_availability !== "available") {
        return false;
      }
      if (!query) {
        return true;
      }
      return submissionName(submission).toLocaleLowerCase("en").includes(query);
    });
  }

  function setSort(key) {
    if (state.sortKey === key) {
      state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
    } else {
      state.sortKey = key;
      state.sortDirection = key === "model" ? "asc" : "desc";
    }
    renderTable();
  }

  function sortHeader(label, secondaryLabel, key) {
    const th = document.createElement("th");
    th.scope = "col";
    const active = state.sortKey === key;
    th.setAttribute(
      "aria-sort",
      active ? (state.sortDirection === "asc" ? "ascending" : "descending") : "none",
    );

    const button = createElement("button", "sort-button");
    button.type = "button";
    const directionText = active
      ? (state.sortDirection === "asc" ? "descending" : "ascending")
      : (key === "model" ? "ascending" : "descending");
    button.setAttribute("aria-label", `Sort by ${label}, ${directionText}`);
    button.addEventListener("click", () => setSort(key));

    const labelContainer = createElement("span", "sort-label");
    labelContainer.append(createElement("span", null, label));
    if (secondaryLabel) {
      labelContainer.append(createElement("small", null, secondaryLabel));
    }
    const indicator = createElement(
      "span",
      "sort-indicator",
      active ? (state.sortDirection === "asc" ? "↑" : "↓") : "↕",
    );
    indicator.setAttribute("aria-hidden", "true");
    button.append(labelContainer, indicator);
    th.append(button);
    return th;
  }

  function renderTableHeader(datasets) {
    elements.tableHead.replaceChildren();
    const row = document.createElement("tr");
    row.append(sortHeader("Model", "Name / variant", "model"));
    datasets.forEach((dataset) => {
      const task = state.tasksById.get(dataset.task);
      row.append(sortHeader(dataset.display_name, task ? task.metric_label : dataset.metric, dataset.id));
    });
    elements.tableHead.append(row);
  }

  function appendMiniBadge(container, text, className) {
    const badge = createElement("span", `mini-badge${className ? ` ${className}` : ""}`, text);
    container.append(badge);
  }

  function renderModelCell(row, submission) {
    const cell = document.createElement("td");
    const button = createElement("button", "model-button");
    button.type = "button";
    button.setAttribute("aria-label", `View details for ${submissionName(submission)}`);
    button.append(
      createElement("strong", null, submission.model_name),
      createElement("span", null, submission.model_variant),
    );
    button.addEventListener("click", () => openDialog(submission, button));
    cell.append(button);

    const badges = createElement("div", "model-cell-badges");
    if (submission.code_availability === "available") {
      appendMiniBadge(badges, "Code", "code");
    }
    if (submission.verification === "reproduced") {
      appendMiniBadge(badges, "Reproduced", "reproduced");
    }
    if (submission.method_type === "in_context") {
      appendMiniBadge(badges, "In-context");
    }
    if (badges.childElementCount) {
      cell.append(badges);
    }
    row.append(cell);
  }

  function renderResultCell(row, submission, dataset) {
    const cell = document.createElement("td");

    // Availability takes precedence over the presence or absence of a submitted result.
    if (!isSettingAvailable(dataset, state.setting)) {
      cell.className = "metric-na";
      cell.dataset.state = "unavailable";
      cell.textContent = "N/A";
      cell.setAttribute(
        "aria-label",
        `${dataset.display_name} is not available in the ${state.setting} setting`,
      );
      row.append(cell);
      return;
    }

    const result = getResult(submission, state.setting, dataset.id);
    if (!result) {
      cell.className = "metric-missing";
      cell.dataset.state = "missing";
      cell.textContent = "—";
      cell.setAttribute("aria-label", `No result submitted for ${dataset.display_name}`);
      row.append(cell);
      return;
    }

    cell.className = "metric-value";
    cell.dataset.state = "value";
    cell.textContent = formatMetric(result, dataset);
    cell.title = `Canonical value: ${result.value}${Number.isFinite(result.std) ? `; standard deviation: ${result.std}` : ""}`;
    row.append(cell);
  }

  function updateEmptyState(submissionCount) {
    const hasAnySubmissions = Boolean(state.payload && state.payload.submissions.length);
    const isEmpty = submissionCount === 0;
    elements.tableScroll.hidden = isEmpty;
    elements.emptyState.hidden = !isEmpty;

    if (!isEmpty) {
      return;
    }

    if (hasAnySubmissions) {
      elements.emptyTitle.textContent = "No models match these filters";
      elements.emptyCopy.textContent = "Try a different model name or turn off the code availability filter.";
      elements.emptyLink.hidden = true;
    } else {
      elements.emptyTitle.textContent = "No results yet";
      elements.emptyCopy.textContent = "Be the first to submit results for GraphLand. Maintainers can add standard open baselines when authoritative results become available.";
      elements.emptyLink.hidden = false;
    }
  }

  function renderTable() {
    if (!state.payload) {
      return;
    }

    const datasets = taskDatasets();
    const task = state.tasksById.get(state.task);
    const submissions = filteredSubmissions().sort((left, right) => compareRows(left, right, {
      key: state.sortKey,
      direction: state.sortDirection,
      setting: state.setting,
      datasetsById: state.datasetsById,
    }));

    renderTableHeader(datasets);
    elements.tableBody.replaceChildren();
    submissions.forEach((submission) => {
      const row = document.createElement("tr");
      renderModelCell(row, submission);
      datasets.forEach((dataset) => renderResultCell(row, submission, dataset));
      elements.tableBody.append(row);
    });

    const modelLabel = submissions.length === 1 ? "model" : "models";
    elements.resultSummary.textContent = `${submissions.length} ${modelLabel} · ${task ? task.label : ""}`;
    elements.tableCaption.textContent = `${state.setting} ${task ? task.label : "GraphLand"} leaderboard`;
    updateEmptyState(submissions.length);
  }

  function render() {
    setActiveTabs();
    updateSettingDescription();
    updateMetricNote();
    renderTable();
  }

  function activateSetting(setting, updateUrl = true) {
    if (!SETTING_IDS.includes(setting)) {
      return;
    }
    state.setting = setting;
    if (updateUrl) {
      writeQueryState();
    }
    render();
  }

  function activateTask(task, updateUrl = true) {
    if (!TASK_IDS.includes(task)) {
      return;
    }
    state.task = task;
    state.sortKey = "model";
    state.sortDirection = "asc";
    if (updateUrl) {
      writeQueryState();
    }
    render();
  }

  function bindTablist(tablist, dataKey, activate) {
    tablist.addEventListener("click", (event) => {
      const tab = event.target.closest("[role='tab']");
      if (!tab || !tablist.contains(tab)) {
        return;
      }
      activate(tab.dataset[dataKey]);
    });

    tablist.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        return;
      }
      const tabs = Array.from(tablist.querySelectorAll("[role='tab']"));
      const current = tabs.indexOf(document.activeElement);
      if (current < 0) {
        return;
      }
      event.preventDefault();
      let next = current;
      if (event.key === "ArrowRight") {
        next = (current + 1) % tabs.length;
      } else if (event.key === "ArrowLeft") {
        next = (current - 1 + tabs.length) % tabs.length;
      } else if (event.key === "Home") {
        next = 0;
      } else if (event.key === "End") {
        next = tabs.length - 1;
      }
      tabs[next].focus();
      activate(tabs[next].dataset[dataKey]);
    });
  }

  function closeNavigation() {
    elements.header.classList.remove("nav-open");
    elements.menuButton.setAttribute("aria-expanded", "false");
    document.body.classList.remove("nav-open");
  }

  function bindNavigation() {
    elements.menuButton.addEventListener("click", () => {
      const opening = !elements.header.classList.contains("nav-open");
      elements.header.classList.toggle("nav-open", opening);
      elements.menuButton.setAttribute("aria-expanded", String(opening));
      document.body.classList.toggle("nav-open", opening);
    });
    elements.navigation.addEventListener("click", (event) => {
      if (event.target.closest("a")) {
        closeNavigation();
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && elements.header.classList.contains("nav-open")) {
        closeNavigation();
        elements.menuButton.focus();
      }
    });
    window.matchMedia("(min-width: 821px)").addEventListener("change", (event) => {
      if (event.matches) {
        closeNavigation();
      }
    });
  }

  function addBadge(container, label, variant) {
    const badge = createElement("span", `badge${variant ? ` ${variant}` : ""}`, label);
    container.append(badge);
  }

  function appendDetail(list, label, value, url) {
    const row = document.createElement("div");
    const term = createElement("dt", null, label);
    const description = document.createElement("dd");
    const safeUrl = safeExternalUrl(url);
    if (safeUrl) {
      const link = createElement("a", null, value || safeUrl);
      link.href = safeUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      description.append(link);
    } else {
      description.textContent = value === null || value === undefined || value === "" ? "Not provided" : String(value);
    }
    row.append(term, description);
    list.append(row);
  }

  function provenanceLabel(value) {
    return value === "maintainer_seeded" ? "Maintainer-added" : "Author submission";
  }

  function verificationLabel(value) {
    return value === "reproduced" ? "Reproduced" : "Self-reported";
  }

  function methodLabel(value) {
    return value === "in_context" ? "In-context learning" : "Trained";
  }

  function reviewStatusLabel(review) {
    return review && review.status === "approved" ? "Approved" : "Pending";
  }

  function issueUrl(submission) {
    const repository = state.payload && state.payload.config && state.payload.config.site
      ? safeExternalUrl(state.payload.config.site.repository_url)
      : null;
    if (!repository || !Number.isInteger(submission.source_issue)) {
      return null;
    }
    return `${repository.replace(/\/$/, "")}/issues/${submission.source_issue}`;
  }

  function openDialog(submission, trigger) {
    const demo = isDemoSubmission(submission);
    state.dialogTrigger = trigger;
    elements.dialogTitle.textContent = submission.model_name;
    elements.dialogContent.replaceChildren();

    const badges = createElement("div", "dialog-badges");
    addBadge(
      badges,
      submission.code_availability === "available" ? "Code available" : "Code unavailable",
      submission.code_availability === "available" ? "positive" : "",
    );
    addBadge(badges, provenanceLabel(submission.provenance));
    addBadge(
      badges,
      verificationLabel(submission.verification),
      submission.verification === "reproduced" ? "accent" : "",
    );
    if (submission.method_type === "in_context") {
      addBadge(badges, "In-context", "accent");
    }

    const lead = createElement("p", "dialog-lead", submission.model_variant);
    const details = createElement("dl", "detail-list");
    appendDetail(details, "Submission ID", submission.id);
    appendDetail(
      details,
      demo ? "Demo documentation" : "Paper",
      submission.paper_url,
      submission.paper_url,
    );
    appendDetail(
      details,
      "Code availability",
      submission.code_availability === "available" ? "Available" : "Unavailable",
    );
    appendDetail(
      details,
      "Training code",
      submission.code_availability === "available" ? submission.training_code_url : "Not published",
      submission.training_code_url,
    );
    appendDetail(
      details,
      "Submitter",
      `@${submission.submitter_github}`,
      `https://github.com/${encodeURIComponent(submission.submitter_github)}`,
    );
    appendDetail(
      details,
      "Source issue",
      demo ? "Not applicable (synthetic demo)" : `#${submission.source_issue}`,
      demo ? null : issueUrl(submission),
    );
    appendDetail(details, "GraphLand version", submission.graphland_ref);
    appendDetail(details, "Method type", methodLabel(submission.method_type));
    appendDetail(details, "Tuning protocol", submission.tuning_protocol);
    appendDetail(details, "Hyperparameter trials", submission.hparam_trials);
    appendDetail(details, "Runs / seeds", submission.num_runs);
    appendDetail(details, "External data / pretraining", submission.external_data_pretraining);
    appendDetail(details, "Provenance", provenanceLabel(submission.provenance));
    appendDetail(details, "Verification", verificationLabel(submission.verification));
    appendDetail(details, "Submission date", submission.submitted_at);
    appendDetail(details, "Notes", submission.notes);

    const review = submission.review || {};
    appendDetail(details, "Review status", reviewStatusLabel(review));
    appendDetail(
      details,
      "Reviewer",
      review.reviewer_github ? `@${review.reviewer_github}` : null,
      review.reviewer_github ? `https://github.com/${encodeURIComponent(review.reviewer_github)}` : null,
    );
    appendDetail(details, "Reviewed at", review.reviewed_at);
    appendDetail(details, "Review notes", review.notes);

    elements.dialogContent.append(badges, lead, details);
    if (typeof elements.dialog.showModal === "function") {
      elements.dialog.showModal();
    } else {
      elements.dialog.setAttribute("open", "");
    }
  }

  function closeDialog() {
    if (typeof elements.dialog.close === "function") {
      elements.dialog.close();
    } else {
      elements.dialog.removeAttribute("open");
    }
  }

  function bindDialog() {
    elements.dialogClose.addEventListener("click", closeDialog);
    elements.dialog.addEventListener("click", (event) => {
      if (event.target === elements.dialog) {
        closeDialog();
      }
    });
    elements.dialog.addEventListener("close", () => {
      if (state.dialogTrigger && document.contains(state.dialogTrigger)) {
        state.dialogTrigger.focus();
      }
      state.dialogTrigger = null;
    });
  }

  function bindControls() {
    bindTablist(elements.settingTabs, "setting", activateSetting);
    bindTablist(elements.taskTabs, "task", activateTask);
    elements.search.addEventListener("input", () => {
      state.search = elements.search.value;
      renderTable();
    });
    elements.codeFilter.addEventListener("change", () => {
      state.codeOnly = elements.codeFilter.checked;
      renderTable();
    });
    window.addEventListener("popstate", () => {
      readQueryState();
      render();
    });
  }

  function applyConfiguredLinks() {
    const site = state.payload && state.payload.config ? state.payload.config.site : null;
    const submissionUrl = site ? safeExternalUrl(site.submission_url) : null;
    if (!submissionUrl) {
      return;
    }
    document.querySelectorAll("[data-submission-link]").forEach((link) => {
      link.href = submissionUrl;
    });
  }

  function validatePayload(payload) {
    return Boolean(
      payload
      && payload.config
      && Array.isArray(payload.config.settings)
      && Array.isArray(payload.config.task_families)
      && Array.isArray(payload.datasets)
      && Array.isArray(payload.submissions),
    );
  }

  async function loadData() {
    const response = await fetch("data/leaderboard.json", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`Leaderboard data request failed with status ${response.status}`);
    }
    const payload = await response.json();
    if (!validatePayload(payload)) {
      throw new Error("Leaderboard data has an unexpected shape");
    }
    return payload;
  }

  async function init() {
    cacheElements();
    readQueryState();
    setActiveTabs();
    bindNavigation();
    bindControls();
    bindDialog();

    try {
      state.payload = await loadData();
      state.datasetsById = new Map(state.payload.datasets.map((dataset) => [dataset.id, dataset]));
      state.settingsById = new Map(state.payload.config.settings.map((setting) => [setting.id, setting]));
      state.tasksById = new Map(state.payload.config.task_families.map((task) => [task.id, task]));
      state.codeOnly = Boolean(
        state.payload.config.default_filters
        && state.payload.config.default_filters.only_models_with_code,
      );
      elements.codeFilter.checked = state.codeOnly;
      applyConfiguredLinks();
      elements.demoNotice.hidden = !hasDemoSubmissions(state.payload);
      elements.loadError.hidden = true;
      render();
    } catch (error) {
      elements.resultSummary.textContent = "Leaderboard unavailable";
      elements.tableScroll.hidden = true;
      elements.emptyState.hidden = true;
      elements.loadError.hidden = false;
      console.error(error);
    }
  }

  window.GraphLandLeaderboard = Object.freeze({
    compareRows,
    formatMetric,
    init,
    isSettingAvailable,
    safeExternalUrl,
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
