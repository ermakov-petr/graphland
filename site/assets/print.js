(() => {
  "use strict";

  let snapshot;
  let tables = [];
  let printFocus = null;
  let printCycle = 0;
  let printing = false;
  let printNavigation;

  // Page interaction takes ownership of focus away from a pending print return.
  for (const type of ['pointerdown', 'touchstart', 'keydown', 'hashchange', 'popstate', 'pagehide']) {
    window.addEventListener(type, () => { printFocus = null; }, true);
  }
  window.addEventListener('focusin', (event) => {
    if (event.target !== document.body && event.target !== document.documentElement
        && event.target !== printFocus) printFocus = null;
  }, true);
  function cleanup() {
    snapshot?.remove();
    snapshot = null;
    for (const [source, records] of tables) {
      source.classList.remove('has-print-records');
      records.remove();
    }
    tables = [];
    document.body.classList.remove('printing-model');
  }
  function cloneForPrint(element) {
    const clone = element.cloneNode(true);
    for (const node of [clone, ...clone.querySelectorAll('[id], [tabindex]')]) {
      node.removeAttribute('id'); node.removeAttribute('tabindex');
    }
    // Long protocol/notes fields need paragraph flow rather than a narrow grid cell.
    for (const row of clone.querySelectorAll('.detail-list > div')) {
      if (row.querySelector('dd')?.textContent.length > 600) row.classList.add('print-prose-field');
    }
    // Native buttons with flex children can disappear in repeated PDF headers.
    for (const button of clone.querySelectorAll('.model-button')) {
      const name = document.createElement('div'); name.className = 'print-model-name';
      name.append(...button.childNodes); button.replaceWith(name);
    }
    return clone;
  }
  window.addEventListener('beforeprint', () => {
    printing = true;
    printCycle += 1;
    const active = document.activeElement;
    if (active !== document.body && active !== document.documentElement) {
      printFocus = active;
      printNavigation = { url: location.href, length: history.length, state: history.state };
    }
    else if (!printFocus?.isConnected) printFocus = null;
    // A preview may be cancelled or prepared more than once. Reconcile first.
    cleanup();
    const dialog = document.querySelector('#model-dialog[open]');
    if (dialog) {
      snapshot = document.createElement('section');
      snapshot.className = 'print-model-details shell';
      const title = document.createElement('h1');
      title.textContent = dialog.querySelector('#dialog-title').textContent;
      snapshot.append(title, cloneForPrint(dialog.querySelector('#dialog-content')));
      document.body.append(snapshot);
      document.body.classList.add('printing-model');
      return;
    }
    for (const table of document.querySelectorAll('.data-table')) {
      const header = table.tHead?.rows[0];
      if (!header || ![...table.tBodies].some(body => body.rows.length)) continue;
      const labels = [...header.cells].map(cell =>
        (cell.querySelector('.sort-label') || cell).textContent.trim().replace(/\s+/g, ' '));
      const records = document.createElement('div');
      records.className = 'print-tables';
      const context = table.id === 'leaderboard-table'
        ? ['GraphLand', ...document.querySelectorAll('.tabs [aria-selected="true"]')].map(item => typeof item === 'string' ? item : item.textContent.trim()).join(' · ')
        : 'GraphLand · Dataset overview';
      for (const body of table.tBodies) for (const row of body.rows) {
        const record = document.createElement('table'); record.className = 'print-record';
        const columns = document.createElement('colgroup');
        for (const width of ['43%', '57%']) { const column = document.createElement('col'); column.style.width = width; columns.append(column); }
        record.append(columns);
        const heading = document.createElement('th'); heading.colSpan = 2;
        const contextLine = document.createElement('div'); contextLine.className = 'print-context'; contextLine.textContent = context;
        heading.append(contextLine, ...cloneForPrint(row.cells[0]).childNodes);
        record.createTHead().insertRow().append(heading);
        const values = record.createTBody();
        [...row.cells].slice(1).forEach((cell, index) => {
          const field = document.createElement('th'); field.scope = 'row'; field.textContent = labels[index + 1];
          const value = cloneForPrint(cell);
          values.insertRow().append(field, value);
        });
        records.append(record);
      }
      table.after(records); table.classList.add('has-print-records'); tables.push([table, records]);
    }
  });
  window.addEventListener('afterprint', () => {
    cleanup();
    printing = false;
    const cycle = printCycle;
    window.requestAnimationFrame(() => {
      if (printing || cycle !== printCycle) return;
      const target = printFocus;
      printFocus = null;
      if (!target) return;
      // pushState/replaceState do not dispatch popstate or hashchange.
      if (location.href !== printNavigation.url || history.length !== printNavigation.length
          || history.state !== printNavigation.state) return;
      // Firefox can drop focus to body when print CSS hides the source table.
      // Restore only an available original target, without replacing user focus.
      if (!target?.isConnected || !target.getClientRects().length || target.matches(':disabled')
          || getComputedStyle(target).visibility !== 'visible'
          || (document.activeElement !== document.body && document.activeElement !== document.documentElement)) return;
      target.focus({ preventScroll: true });
    });
  });
})();
