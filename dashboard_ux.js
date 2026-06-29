/* ========================================================================== */
/* World-class dashboard UX engine
 * legacy-test-marker: 7.9.0-no-hero-stable-direct-canvas                                             */
/* Universal chart actions, chart studio, command palette, page map.    */
/* ========================================================================== */
(function () {
  'use strict';
  if (window.DashboardUX && window.DashboardUX.version) return;

  const UX = window.DashboardUX = { version: '7.11.0-verified-visible-charts' };
  document.documentElement.dataset.dashboardUx = 'root-ready';
  let studioChart = null;
  let observerQueued = false;
  let lastActiveElement = null;

  const pageMeta = (() => {
    const path = document.body?.dataset?.dashboardPage || location.pathname || '/';
    if (path.includes('csat')) {
      return {
        key: 'csat',
        eyebrow: 'Domo-first quality intelligence',
        title: 'Call Quality Command Center',
        subtitle: 'A premium CSAT workspace for leaders and analysts: Domo-first data, open call-level drilldowns, team and consultant analysis, and consistent chart inspection everywhere.',
        source: 'Domo dataset',
        primary: 'CSAT, solved rate, true FCR, teams',
        dataNote: 'Domo-backed raw drilldowns are available directly inside the dashboard.'
      };
    }
    if (path.includes('starter-guides')) {
      return {
        key: 'starter-guides',
        eyebrow: 'Journey intelligence',
        title: 'Starter Guides Experience Center',
        subtitle: 'Journey search, guide completion, slide engagement, and answer behavior in one guided workspace built for fast support investigation.',
        source: 'CMS starter-guide events',
        primary: 'Journeys, guides, answers, completion',
        dataNote: 'Search a customer journey first, then use Metrics for aggregate patterns.'
      };
    }
    return {
      key: 'portal',
      eyebrow: 'Executive portal intelligence',
      title: 'CS Portal Activity Center',
      subtitle: 'A redesigned operating dashboard for engagement, self-service behavior, content health, and analyst-grade drilldowns across the selected period.',
      source: 'CMS metrics API',
      primary: 'Usage, search, sessions, content',
      dataNote: 'Article estimates are called out when source granularity is limited.'
    };
  })();

  function cssVar(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim() || getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }
  function slug(value) {
    return String(value || 'chart').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 70) || 'chart';
  }
  function fmtNumber(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value ?? '');
    if (Math.abs(n) >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
    if (Math.abs(n) >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
    return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.00$/, '');
  }
  function valueToScalar(value) {
    if (value == null) return null;
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value === 'object') {
      const candidate = value.y ?? value.v ?? value.value ?? value.count;
      const n = Number(candidate);
      return Number.isFinite(n) ? n : null;
    }
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  function toast(message) {
    let el = document.getElementById('uxToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'uxToast';
      el.className = 'ux-toast';
      el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.classList.remove('show'), 2600);
  }
  function downloadText(filename, text, type) {
    const blob = new Blob([text], { type: type || 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(() => true).catch(() => false);
    }
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.focus();
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
    area.remove();
    return Promise.resolve(ok);
  }

  function applyPrefs() {
    try { localStorage.removeItem('dashboard.theme'); } catch (_) {}
    let density = 'comfortable';
    try { density = localStorage.getItem('dashboard.density') || 'comfortable'; } catch (_) { density = 'comfortable'; }
    document.body.removeAttribute('data-theme');
    document.body.dataset.density = density;
    recolorAllCharts();
    updateHeroStats();
  }
  function toggleDensity() {
    const next = document.body.dataset.density === 'compact' ? 'comfortable' : 'compact';
    document.body.dataset.density = next;
    localStorage.setItem('dashboard.density', next);
    toast(next === 'compact' ? 'Compact analyst density enabled' : 'Comfortable layout enabled');
  }
  function togglePresentation() {
    const enabled = document.body.dataset.presentation !== 'true';
    document.body.dataset.presentation = enabled ? 'true' : 'false';
    toast(enabled ? 'Presentation mode enabled' : 'Presentation mode disabled');
  }

  function isUxInternalCanvas(canvas) {
    const id = String(canvas?.id || '');
    return !id || id.startsWith('ux') || id === 'chartModalCanvas';
  }
  function chartInstances() {
    if (!window.Chart) return [];
    const canvases = Array.from(document.querySelectorAll('canvas[id]')).filter(c => !isUxInternalCanvas(c));
    return canvases.map(c => Chart.getChart(c)).filter(Boolean);
  }

  function ensureCanvasStage(canvas, wrap) {
    // v7.9 stable rendering fix: keep live Chart.js canvases in their
    // original parent. Moving Portal Activity canvases after Chart.js has
    // created them can make dashboard charts appear blank while expanded
    // clones still render correctly.
    if (!canvas || isUxInternalCanvas(canvas)) return canvas?.parentElement || null;
    if (canvas.closest('#uxChartStudio, #chartModal')) return canvas.parentElement || null;
    const stage = canvas.parentElement && canvas.parentElement.classList && canvas.parentElement.classList.contains('ux-canvas-stage') ? canvas.parentElement : null;
    if (stage) {
      const parent = stage.parentElement;
      if (parent) {
        parent.insertBefore(canvas, stage);
        stage.remove();
      }
    }
    canvas.classList.add('ux-staged');
    return wrap || canvas.closest('.chart-wrap') || canvas.parentElement;
  }

  function scheduleChartResize(chart) {
    // v7.12: CSS (portal-overrides.css v7.12 block) owns canvas positioning.
    // This function must NOT set inline position/size styles on the canvas —
    // that created an arms-race with !important CSS rules and caused clipping.
    // We only: (a) add the right classes to the wrap so CSS kicks in,
    //          (b) call chart.resize() so Chart.js re-measures the now-correct box.
    if (!chart || !chart.canvas || isUxInternalCanvas(chart.canvas)) return;
    const run = () => {
      try {
        const canvas = chart.canvas;
        if (!canvas || canvas.closest('#uxChartStudio, #chartModal')) return;
        const wrap = canvas.closest('.chart-wrap') || canvas.closest('.ux-chart-wrap') || canvas.parentElement;
        if (!wrap) return;

        // Add classes so the CSS v7.12 block applies the correct layout.
        wrap.classList.add('ux-chart-wrap', 'ux-direct-canvas', 'ux-render-locked', 'ux-axis-safe-chart');
        const id = String(canvas.id || '').toLowerCase();
        const isMini = id.includes('hourinsight') || id === 'hourchart' || id.includes('mini');
        wrap.classList.toggle('ux-mini-chart', isMini);

        // Strip any conflicting inline canvas geometry from older code paths.
        canvas.style.removeProperty('position');
        canvas.style.removeProperty('top');
        canvas.style.removeProperty('left');
        canvas.style.removeProperty('right');
        canvas.style.removeProperty('bottom');
        canvas.style.removeProperty('width');
        canvas.style.removeProperty('height');
        canvas.style.removeProperty('max-width');
        canvas.style.removeProperty('max-height');
        canvas.style.removeProperty('margin');
        // Ensure visibility is set (non-layout properties are safe).
        canvas.style.display    = 'block';
        canvas.style.visibility = 'visible';
        canvas.style.opacity    = '1';
        canvas.style.zIndex     = '1';

        // Let Chart.js re-measure the canvas bounding box.
        if (typeof chart.resize === 'function') chart.resize();
        if (typeof chart.update === 'function') chart.update('none');
      } catch (_) {}
    };
    requestAnimationFrame(run);
    setTimeout(run, 80);
    setTimeout(run, 260);
    setTimeout(run, 600);
    setTimeout(run, 1400);
  }

  function polishChartLayout(chart) {
    if (!chart || !chart.options || !chart.canvas || isUxInternalCanvas(chart.canvas) || chart.canvas.closest('#uxChartStudio, #chartModal')) return;
    const canvas = chart.canvas;
    const wrap = canvas.closest('.chart-wrap') || canvas.closest('.ux-chart-wrap') || canvas.parentElement;
    if (wrap) {
      wrap.classList.add('ux-axis-safe-chart', 'ux-direct-canvas', 'ux-render-locked');
      ensureCanvasStage(canvas, wrap);
      const id = String(canvas.id || '').toLowerCase();
      const mini = id.includes('hourinsight') || id === 'hourchart' || id.includes('mini');
      wrap.classList.toggle('ux-mini-chart', !!mini);
    }
    chart.options.responsive = true;
    chart.options.maintainAspectRatio = false;
    chart.options.interaction = Object.assign({ mode: 'nearest', intersect: false }, chart.options.interaction || {});
    chart.options.hover = Object.assign({ mode: 'nearest', intersect: false }, chart.options.hover || {});
    chart.options.plugins = chart.options.plugins || {};
    chart.options.plugins.tooltip = Object.assign({
      enabled: true,
      displayColors: true,
      backgroundColor: '#ffffff',
      borderColor: '#d8e0ef',
      borderWidth: 1,
      titleColor: '#0f172a',
      bodyColor: '#334155',
      padding: 12,
      cornerRadius: 12,
      caretSize: 6,
      titleFont: { weight: '800', size: 12 },
      bodyFont: { weight: '650', size: 12 }
    }, chart.options.plugins.tooltip || {});
    chart.options.layout = chart.options.layout || {};
    const oldPadding = typeof chart.options.layout.padding === 'object' ? chart.options.layout.padding : {};
    chart.options.layout.padding = Object.assign({ top: 10, right: 14, bottom: 38, left: 8 }, oldPadding, { bottom: Math.max(Number(oldPadding.bottom || 0), 38) });
    const scales = chart.options.scales || {};
    Object.entries(scales).forEach(([id, scale]) => {
      if (!scale || typeof scale !== 'object') return;
      scale.ticks = scale.ticks || {};
      scale.ticks.display = true;
      scale.ticks.color = scale.ticks.color || (cssVar('--text3') || '#64748b');
      scale.ticks.font = Object.assign({ size: 11, weight: '650' }, scale.ticks.font || {});
      scale.ticks.padding = Math.max(Number(scale.ticks.padding || 0), 8);
      if (String(id).startsWith('x')) {
        scale.display = scale.display !== false;
        scale.ticks.autoSkip = true;
        scale.ticks.maxTicksLimit = scale.ticks.maxTicksLimit || 9;
        scale.ticks.minRotation = 0;
        scale.ticks.maxRotation = Math.max(Number(scale.ticks.maxRotation || 0), 24);
      }
      if (String(id).startsWith('y')) {
        scale.ticks.autoSkip = scale.ticks.autoSkip ?? false;
        scale.ticks.maxTicksLimit = scale.ticks.maxTicksLimit || 10;
      }
    });
    scheduleChartResize(chart);
  }

  function polishAllChartLayouts() {
    if (!window.Chart) return;
    chartInstances().forEach(chart => {
      try { polishChartLayout(chart); scheduleChartResize(chart); } catch (_) {}
    });
  }

  function recolorAllCharts() {
    if (!window.Chart) return;
    const text = cssVar('--text3') || '#64748b';
    const grid = cssVar('--chart-grid') || cssVar('--border') || '#dde1ec';
    const title = cssVar('--text2') || text;
    Chart.defaults.color = text;
    Chart.defaults.borderColor = grid;
    chartInstances().forEach(chart => {
      try {
        polishChartLayout(chart);
        if (chart.options?.scales) {
          Object.values(chart.options.scales).forEach(scale => {
            if (scale.grid) scale.grid.color = grid;
            if (scale.ticks) scale.ticks.color = text;
            if (scale.title) scale.title.color = title;
          });
        }
        const plugins = chart.options.plugins || {};
        if (plugins.legend?.labels) plugins.legend.labels.color = text;
        if (plugins.title) plugins.title.color = title;
        chart.update('none');
      } catch (_) {}
    });
  }

  function injectSkipLink() {
    if (document.querySelector('.ux-skip')) return;
    const skip = document.createElement('a');
    skip.className = 'ux-skip';
    skip.href = '#appRoot';
    skip.textContent = 'Skip to dashboard';
    document.body.insertBefore(skip, document.body.firstChild);
  }

  function injectHero() {
    // v7.9: remove the large UX banner/hero on every page.
    document.querySelectorAll('#uxHero, .ux-hero').forEach(el => el.remove());
    return;
  }

  function buildPageMap() {
    if (document.getElementById('uxPageMap')) return;
    const header = document.querySelector('.ux-hero') || document.querySelector('.header');
    if (!header) return;
    const seenTitles = new Set();
    const titles = Array.from(document.querySelectorAll('.section-title, .card-title'))
      .filter(el => {
        if (el.closest('.ux-modal, .ux-chart-toolbar, .ux-chart-data-panel')) return false;
        const text = (el.textContent || '').trim();
        if (!text || text.length < 3 || seenTitles.has(text)) return false;
        const visible = el.offsetParent !== null || el.closest('#s4') || el.closest('#s1') || el.closest('#s2') || el.closest('#s3');
        if (!visible) return false;
        seenTitles.add(text);
        return true;
      })
      .slice(0, 12);
    if (!titles.length) return;
    const nav = document.createElement('nav');
    nav.id = 'uxPageMap';
    nav.className = 'ux-page-map';
    nav.setAttribute('aria-label', 'Sections on this page');
    titles.forEach((el, idx) => {
      if (!el.id) el.id = 'ux-section-' + idx + '-' + slug(el.textContent);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = el.textContent.trim();
      btn.addEventListener('click', () => el.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      nav.appendChild(btn);
    });
    header.insertAdjacentElement('afterend', nav);

    const buttons = Array.from(nav.querySelectorAll('button'));
    const obs = new IntersectionObserver(entries => {
      const visible = entries.filter(e => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      const idx = titles.indexOf(visible.target);
      buttons.forEach((b, i) => b.classList.toggle('ux-active', i === idx));
    }, { rootMargin: '-18% 0px -70% 0px', threshold: [0, .25, .5, 1] });
    titles.forEach(t => obs.observe(t));
  }

  function titleForCanvas(canvas) {
    const card = canvas.closest('.card, .section, .journey-card') || canvas.parentElement;
    const title = card?.querySelector('.card-title, .section-title, h2, h3')?.textContent?.trim();
    return title || canvas.getAttribute('aria-label') || canvas.id || 'Dashboard chart';
  }
  function getChartById(id) {
    if (!window.Chart) return null;
    const canvas = document.getElementById(id);
    return canvas ? Chart.getChart(canvas) : null;
  }
  function pruneStaleChartUx(canvasIds) {
    const live = canvasIds || new Set(Array.from(document.querySelectorAll('canvas[id]')).map(c => c.id));
    document.querySelectorAll('.ux-chart-toolbar[data-chart-id]').forEach(el => {
      if (!live.has(el.dataset.chartId)) el.remove();
    });
    document.querySelectorAll('.ux-chart-data-panel[id^="ux-data-panel-"]').forEach(el => {
      const id = el.id.replace('ux-data-panel-', '');
      if (!live.has(id)) el.remove();
    });
  }

  function enhanceCharts() {
    const canvases = Array.from(document.querySelectorAll('canvas[id]')).filter(canvas => !canvas.id.startsWith('ux') && canvas.id !== 'chartModalCanvas');
    pruneStaleChartUx(new Set(canvases.map(c => c.id)));
    canvases.forEach(canvas => {
      if (canvas.dataset.uxEnhanced === '1') return;
      const wrap = canvas.closest('.chart-wrap') || canvas.parentElement;
      if (!wrap) return;
      document.querySelectorAll('.ux-chart-toolbar').forEach(el => { if (el.dataset.chartId === canvas.id) el.remove(); });
      const stalePanel = document.getElementById('ux-data-panel-' + canvas.id);
      if (stalePanel) stalePanel.remove();
      canvas.dataset.uxEnhanced = '1';
      wrap.classList.add('ux-chart-wrap');
      const stage = ensureCanvasStage(canvas, wrap);
      canvas.setAttribute('tabindex', '0');
      canvas.setAttribute('role', 'img');
      canvas.setAttribute('aria-label', titleForCanvas(canvas));

      const title = titleForCanvas(canvas);
      const toolbar = document.createElement('div');
      toolbar.className = 'ux-chart-toolbar';
      toolbar.dataset.chartId = canvas.id;
      toolbar.innerHTML = `
        <div class="ux-chart-toolbar-left">
          <div class="ux-chart-kicker"><span class="ux-chart-name">${esc(title)}</span></div>
        </div>
        <div class="ux-chart-actions" role="toolbar" aria-label="Chart actions for ${esc(title)}">
          <button type="button" class="ux-chart-action" data-ux-action="insight" data-chart-id="${esc(canvas.id)}" title="Show chart insight" aria-label="Show chart insight"><span aria-hidden="true">✦</span><span>Insight</span></button>
          <button type="button" class="ux-chart-action" data-ux-action="data" data-chart-id="${esc(canvas.id)}" title="View data table" aria-label="View chart data table"><span aria-hidden="true">▦</span><span>Data</span></button>
          <button type="button" class="ux-chart-action" data-ux-action="csv" data-chart-id="${esc(canvas.id)}" title="Download CSV" aria-label="Download chart data as CSV"><span>CSV</span></button>
          <button type="button" class="ux-chart-action" data-ux-action="png" data-chart-id="${esc(canvas.id)}" title="Download PNG" aria-label="Download chart image as PNG"><span>PNG</span></button>
          <button type="button" class="ux-chart-action primary" data-ux-action="expand" data-chart-id="${esc(canvas.id)}" title="Expand chart" aria-label="Expand chart"><span aria-hidden="true">⤢</span><span>Expand</span></button>
        </div>`;
      const panel = document.createElement('div');
      panel.className = 'ux-chart-data-panel';
      panel.id = 'ux-data-panel-' + canvas.id;
      panel.setAttribute('aria-live', 'polite');
      wrap.querySelectorAll('.chart-expand-btn, .ux-generated-expand').forEach(btn => btn.remove());
      wrap.insertBefore(toolbar, wrap.firstChild);
      wrap.insertAdjacentElement('afterend', panel);
      installCanvasInspector(canvas);
      const chart = getChartById(canvas.id);
      if (chart) { try { polishChartLayout(chart); scheduleChartResize(chart); } catch (_) {} }
    });
    polishAllChartLayouts();
    updateHeroStats();
  }

  function updateHeroStats() {
    const el = document.getElementById('uxChartCount');
    if (el) el.textContent = String(document.querySelectorAll('canvas[data-ux-enhanced="1"]').length);
  }

  function chartToRows(chart) {
    const labels = Array.isArray(chart?.data?.labels) ? chart.data.labels : [];
    const datasets = Array.isArray(chart?.data?.datasets) ? chart.data.datasets : [];
    return labels.map((label, i) => {
      const row = { label: label ?? '' };
      datasets.forEach((ds, di) => {
        const name = ds.label || ('Series ' + (di + 1));
        row[name] = valueToScalar(ds.data?.[i]) ?? ds.data?.[i] ?? '';
      });
      return row;
    });
  }
  function rowsToCsv(rows) {
    if (!rows.length) return 'label\n';
    const headers = Array.from(rows.reduce((set, row) => {
      Object.keys(row).forEach(k => set.add(k));
      return set;
    }, new Set()));
    const quote = value => '"' + String(value ?? '').replace(/"/g, '""') + '"';
    return [headers.map(quote).join(',')].concat(rows.map(row => headers.map(h => quote(row[h])).join(','))).join('\n');
  }
  function chartInsight(chart) {
    const title = titleForCanvas(chart.canvas);
    const labels = chart.data?.labels || [];
    const datasets = chart.data?.datasets || [];
    const lines = [];
    lines.push(`${title}: ${labels.length} visible category/time point${labels.length === 1 ? '' : 's'} across ${datasets.length} series.`);
    datasets.forEach(ds => {
      const nums = (ds.data || []).map(valueToScalar).filter(v => Number.isFinite(v));
      if (!nums.length) return;
      const total = nums.reduce((a, b) => a + b, 0);
      let maxIdx = 0;
      let minIdx = 0;
      nums.forEach((v, i) => { if (v > nums[maxIdx]) maxIdx = i; if (v < nums[minIdx]) minIdx = i; });
      const first = nums[0];
      const last = nums[nums.length - 1];
      const delta = first ? ((last - first) / Math.abs(first)) * 100 : null;
      const label = ds.label || 'Series';
      const topLabel = labels[maxIdx] ?? ('point ' + (maxIdx + 1));
      lines.push(`${label} totals ${fmtNumber(total)}; peak is ${topLabel} at ${fmtNumber(nums[maxIdx])}.`);
      if (Number.isFinite(delta) && nums.length > 1) {
        const direction = delta > 2 ? 'up' : delta < -2 ? 'down' : 'flat';
        lines.push(`${label} ends ${direction} vs the first visible point (${delta > 0 ? '+' : ''}${delta.toFixed(1)}%).`);
      }
    });
    if (labels.length < 4) lines.push('Sample is small; treat this view as directional until more points are loaded.');
    return lines.join(' ');
  }

  function renderDataPanel(chart, mode) {
    const canvasId = chart.canvas.id;
    const panel = document.getElementById('ux-data-panel-' + canvasId);
    if (!panel) return;
    const rows = chartToRows(chart);
    const headers = rows.length ? Object.keys(rows[0]) : ['label'];
    const bodyRows = rows.slice(0, 250).map(row => `<tr>${headers.map(h => `<td>${esc(row[h])}</td>`).join('')}</tr>`).join('');
    const insight = chartInsight(chart);
    panel.innerHTML = `
      <div class="ux-chart-data-panel-header">
        <span>${mode === 'insight' ? 'Analyst insight' : 'Underlying chart data'} · ${rows.length} row${rows.length === 1 ? '' : 's'}</span>
        <button type="button" class="ux-mini-btn" data-ux-action="close-panel" data-chart-id="${esc(canvasId)}">Close</button>
      </div>
      ${mode === 'insight' ? `<div class="ux-insight-box"><strong>Readout:</strong> ${esc(insight)}</div>` : ''}
      <div class="ux-chart-data-scroll">
        <table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${bodyRows || `<tr><td>No chart data available yet.</td></tr>`}</tbody></table>
      </div>`;
    panel.classList.add('open');
  }

  function installCanvasInspector(canvas) {
    if (canvas.dataset.uxInspector === '1') return;
    canvas.dataset.uxInspector = '1';
    const inspect = evt => {
      const chart = getChartById(canvas.id);
      if (!chart) return;
      const points = chart.getElementsAtEventForMode(evt, 'nearest', { intersect: true }, true);
      if (!points.length) return;
      const point = points[0];
      const ds = chart.data.datasets[point.datasetIndex] || {};
      const label = chart.data.labels?.[point.index] ?? ('Point ' + (point.index + 1));
      const raw = ds.data?.[point.index];
      const value = valueToScalar(raw) ?? raw ?? '';
      showPointPopover(evt.clientX, evt.clientY, {
        title: titleForCanvas(canvas),
        label,
        series: ds.label || 'Series',
        value
      });
    };
    canvas.addEventListener('click', inspect);
    canvas.addEventListener('keydown', evt => {
      if (evt.key === 'Enter' || evt.key === ' ') {
        evt.preventDefault();
        openStudio(canvas.id);
      }
    });
  }
  function showPointPopover(x, y, item) {
    document.querySelectorAll('.ux-click-popover').forEach(el => el.remove());
    const el = document.createElement('div');
    el.className = 'ux-click-popover';
    el.innerHTML = `
      <div class="ux-pop-title">${esc(item.title)}</div>
      <div class="ux-pop-row"><span>Point</span><span class="ux-pop-value">${esc(item.label)}</span></div>
      <div class="ux-pop-row"><span>Series</span><span class="ux-pop-value">${esc(item.series)}</span></div>
      <div class="ux-pop-row"><span>Value</span><span class="ux-pop-value">${esc(fmtNumber(item.value))}</span></div>`;
    document.body.appendChild(el);
    const rect = el.getBoundingClientRect();
    el.style.left = Math.min(window.innerWidth - rect.width - 12, Math.max(12, x + 14)) + 'px';
    el.style.top = Math.min(window.innerHeight - rect.height - 12, Math.max(12, y + 14)) + 'px';
    clearTimeout(showPointPopover._timer);
    showPointPopover._timer = setTimeout(() => el.remove(), 4200);
  }

  function downloadPng(chart) {
    try {
      const a = document.createElement('a');
      a.download = slug(titleForCanvas(chart.canvas)) + '.png';
      a.href = chart.toBase64Image('image/png', 1);
      a.click();
      toast('PNG exported');
    } catch (_) {
      toast('This chart cannot be exported as PNG yet');
    }
  }
  function downloadCsv(chart) {
    const rows = chartToRows(chart);
    downloadText(slug(titleForCanvas(chart.canvas)) + '.csv', rowsToCsv(rows), 'text/csv;charset=utf-8');
    toast('CSV exported');
  }
  function exportAllCharts() {
    const charts = chartInstances();
    if (!charts.length) { toast('No chart data is available yet'); return; }
    const blocks = charts.map(chart => {
      const name = titleForCanvas(chart.canvas);
      return '### ' + name + '\n' + rowsToCsv(chartToRows(chart));
    }).join('\n\n');
    downloadText(slug(pageMeta.title) + '-all-chart-data.csv', blocks, 'text/csv;charset=utf-8');
    toast('All chart data exported');
  }

  function ensureStudioModal() {
    let modal = document.getElementById('uxChartStudio');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'uxChartStudio';
    modal.className = 'ux-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.innerHTML = `
      <div class="ux-modal-card">
        <div class="ux-modal-head">
          <div>
            <div class="ux-modal-title" id="uxStudioTitle">Expanded chart studio</div>
            <div class="ux-modal-sub" id="uxStudioSub">Expand the chart, inspect data, export, and copy the readout.</div>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
            <button type="button" class="ux-btn" id="uxStudioCsv">CSV</button>
            <button type="button" class="ux-btn" id="uxStudioPng">PNG</button>
            <button type="button" class="ux-btn" id="uxStudioCopy">Copy insight</button>
            <button type="button" class="ux-btn primary" data-ux-close="studio">Close</button>
          </div>
        </div>
        <div class="ux-modal-body">
          <div class="ux-studio-grid">
            <div class="ux-studio-chart"><canvas id="uxStudioCanvas"></canvas></div>
            <div class="ux-studio-side">
              <div class="ux-studio-panel"><h3>Analyst readout</h3><div class="ux-panel-body" id="uxStudioInsight"></div></div>
              <div class="ux-studio-panel"><h3>Visible data</h3><div class="ux-panel-body" id="uxStudioTable" style="max-height:360px;overflow:auto;padding:0"></div></div>
            </div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal || e.target.closest('[data-ux-close="studio"]')) closeStudio(); });
    return modal;
  }
  function lockBodyForStudio() {
    const scrollBar = window.innerWidth - document.documentElement.clientWidth;
    document.body.dataset.uxStudioOpen = 'true';
    document.body.style.overflow = 'hidden';
    if (scrollBar > 0) document.body.style.paddingRight = scrollBar + 'px';
  }
  function unlockBodyForStudio() {
    document.body.dataset.uxStudioOpen = 'false';
    document.body.style.overflow = '';
    document.body.style.paddingRight = '';
  }

  function openStudio(canvasId) {
    const chart = getChartById(canvasId);
    if (!chart) { toast('Chart is still loading'); return; }
    const modal = ensureStudioModal();
    const title = titleForCanvas(chart.canvas);
    const insight = chartInsight(chart);
    lastActiveElement = document.activeElement;
    document.getElementById('uxStudioTitle').textContent = title;
    document.getElementById('uxStudioSub').textContent = 'Expanded chart studio · hover any point for details, export the data, or copy the readout.';
    document.getElementById('uxStudioInsight').innerHTML = esc(insight).replace(/(peak|totals|ends|Sample)/g, '<strong>$1</strong>');
    const rows = chartToRows(chart);
    const headers = rows.length ? Object.keys(rows[0]) : ['label'];
    document.getElementById('uxStudioTable').innerHTML = `<table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.slice(0, 250).map(row => `<tr>${headers.map(h => `<td>${esc(row[h])}</td>`).join('')}</tr>`).join('') || '<tr><td>No data yet.</td></tr>'}</tbody></table>`;
    modal.classList.add('open');
    lockBodyForStudio();
    if (studioChart) { studioChart.destroy(); studioChart = null; }
    const cfg = {
      type: chart.config.type,
      data: JSON.parse(JSON.stringify(chart.data || {})),
      options: JSON.parse(JSON.stringify(chart.options || {}))
    };
    cfg.options.responsive = true;
    cfg.options.maintainAspectRatio = false;
    cfg.options.animation = false;
    cfg.options.plugins = cfg.options.plugins || {};
    cfg.options.plugins.legend = cfg.options.plugins.legend || {};
    cfg.options.plugins.legend.position = cfg.options.plugins.legend.position || 'bottom';
    const ctx = document.getElementById('uxStudioCanvas').getContext('2d');
    studioChart = new Chart(ctx, cfg);
    try { polishChartLayout(studioChart); studioChart.resize(); studioChart.update('none'); } catch (_) {}
    document.getElementById('uxStudioCsv').onclick = () => downloadCsv(chart);
    document.getElementById('uxStudioPng').onclick = () => downloadPng(chart);
    document.getElementById('uxStudioCopy').onclick = () => copyText(insight).then(ok => toast(ok ? 'Insight copied' : 'Copy failed'));
    modal.querySelector('[data-ux-close="studio"]').focus();
  }
  function restoreDashboardChartsAfterStudio() {
    // Closing the expanded studio changes viewport/body sizing. Chart.js can keep
    // stale canvas measurements from the overlay state, which makes dashboard
    // charts appear blank until a full browser refresh. Force a staged restore.
    const restore = () => {
      try { window.dispatchEvent(new Event('resize')); } catch (_) {}
      if (!window.Chart) return;
      document.querySelectorAll('canvas[id]').forEach(canvas => {
        if (isUxInternalCanvas(canvas) || canvas.closest('#uxChartStudio, #chartModal')) return;
        const chart = Chart.getChart(canvas);
        if (!chart) return;
        try {
          const wrap = canvas.closest('.chart-wrap') || canvas.parentElement;
          ensureCanvasStage(canvas, wrap);
          polishChartLayout(chart);
          scheduleChartResize(chart);
        } catch (_) {}
      });
    };
    requestAnimationFrame(restore);
    setTimeout(restore, 80);
    setTimeout(restore, 260);
    setTimeout(restore, 650);
  }

  UX.restoreCharts = restoreDashboardChartsAfterStudio;
  UX.restoreDashboardCharts = restoreDashboardChartsAfterStudio;
  UX.settleCharts = function settleCharts() {
    enhanceCharts();
    polishAllChartLayouts();
    chartInstances().forEach(scheduleChartResize);
  };

  function closeStudio() {
    const modal = document.getElementById('uxChartStudio');
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    if (modal) modal.classList.remove('open');
    if (studioChart) { try { studioChart.destroy(); } catch (_) {} studioChart = null; }
    unlockBodyForStudio();
    restoreDashboardChartsAfterStudio();
    requestAnimationFrame(() => {
      try { window.scrollTo(scrollX, scrollY); } catch (_) {}
      if (lastActiveElement && typeof lastActiveElement.focus === 'function' && document.contains(lastActiveElement)) {
        try { lastActiveElement.focus({ preventScroll: true }); } catch (_) { try { lastActiveElement.focus(); } catch (_) {} }
      }
    });
  }

  function ensureCommandPalette() {
    let modal = document.getElementById('uxCommandPalette');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'uxCommandPalette';
    modal.className = 'ux-modal ux-command-palette';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.innerHTML = `
      <div class="ux-modal-card">
        <div class="ux-modal-head">
          <div>
            <div class="ux-modal-title">Dashboard command center</div>
            <div class="ux-modal-sub">Search actions, jump between views, and operate the dashboard faster. Press Esc to close.</div>
          </div>
          <button type="button" class="ux-btn primary" data-ux-close="palette">Close</button>
        </div>
        <div class="ux-modal-body">
          <input class="ux-command-input" id="uxCommandInput" placeholder="Type a command, e.g. export, CSAT, clear filters..." autocomplete="off">
          <div class="ux-command-list" id="uxCommandList"></div>
        </div>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal || e.target.closest('[data-ux-close="palette"]')) closePalette(); });
    document.getElementById('uxCommandInput').addEventListener('input', renderCommands);
    return modal;
  }
  function commandDefs() {
    const defs = [
      { title: 'Go to Portal Activity', sub: 'Usage, content, sessions, search', kbd: '/', run: () => { location.href = '/'; } },
      { title: 'Go to Call Quality', sub: 'Domo-first CSAT and call quality', kbd: '/csat', run: () => { location.href = '/csat'; } },
      { title: 'Go to Starter Guides', sub: 'Journey and guide analytics', kbd: '/starter-guides', run: () => { location.href = '/starter-guides'; } },
      { title: 'Toggle density', sub: 'Comfortable vs compact analyst layout', kbd: 'D', run: toggleDensity },
      { title: 'Presentation mode', sub: 'Reduce stickiness and make cards easier to present', kbd: 'P', run: togglePresentation },
      { title: 'Export all chart data', sub: 'Download a combined CSV for every loaded chart', kbd: 'E', run: exportAllCharts },
      { title: 'Expand first chart', sub: 'Open the first loaded chart in the expanded chart studio', kbd: 'F', run: () => { const c = document.querySelector('canvas[data-ux-enhanced="1"]'); if (c) openStudio(c.id); else toast('No chart is loaded yet'); } },
      { title: 'Scroll to top', sub: 'Return to dashboard overview', kbd: 'Home', run: () => window.scrollTo({ top: 0, behavior: 'smooth' }) },
    ];
    if (typeof window.clearAllFilters === 'function') defs.push({ title: 'Clear filters', sub: 'Reset active dashboard filters', kbd: 'C', run: () => window.clearAllFilters() });
    if (typeof window.loadMetrics === 'function') defs.push({ title: 'Reload Starter Guide metrics', sub: 'Refresh aggregate metrics view', kbd: 'R', run: () => window.loadMetrics() });
    if (typeof window.refreshFromDomo === 'function') defs.push({ title: 'Refresh CSAT from Domo', sub: 'Pull latest Domo CSAT data', kbd: 'R', run: () => window.refreshFromDomo() });
    else if (typeof window.refreshCsat === 'function') defs.push({ title: 'Refresh CSAT from Domo', sub: 'CSAT refresh action', kbd: 'R', run: () => window.refreshCsat() });
    return defs;
  }
  function renderCommands() {
    const list = document.getElementById('uxCommandList');
    const query = (document.getElementById('uxCommandInput')?.value || '').toLowerCase().trim();
    if (!list) return;
    const defs = commandDefs().filter(c => !query || (c.title + ' ' + c.sub).toLowerCase().includes(query));
    list.innerHTML = defs.map((c, i) => `
      <div class="ux-command-item" role="button" tabindex="0" data-command-index="${i}">
        <div><div class="ux-command-item-title">${esc(c.title)}</div><div class="ux-command-item-sub">${esc(c.sub)}</div></div>
        <span class="ux-kbd">${esc(c.kbd)}</span>
      </div>`).join('') || '<div class="ux-command-item"><div><div class="ux-command-item-title">No commands found</div><div class="ux-command-item-sub">Try export, chart, CSAT, or filters.</div></div></div>';
    Array.from(list.querySelectorAll('[data-command-index]')).forEach(el => {
      const idx = Number(el.dataset.commandIndex);
      el.addEventListener('click', () => { closePalette(); defs[idx].run(); });
      el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); closePalette(); defs[idx].run(); } });
    });
  }
  function openPalette() {
    const modal = ensureCommandPalette();
    lastActiveElement = document.activeElement;
    modal.classList.add('open');
    lockBodyForStudio();
    renderCommands();
    const input = document.getElementById('uxCommandInput');
    input.value = '';
    setTimeout(() => input.focus(), 0);
  }
  function closePalette() {
    const modal = document.getElementById('uxCommandPalette');
    if (modal) modal.classList.remove('open');
    document.body.style.overflow = '';
    if (lastActiveElement && typeof lastActiveElement.focus === 'function') lastActiveElement.focus();
  }

  function installGlobalHandlers() {
    document.addEventListener('click', e => {
      const chartBtn = e.target.closest('[data-ux-action][data-chart-id]');
      if (chartBtn) {
        const id = chartBtn.dataset.chartId;
        const action = chartBtn.dataset.uxAction;
        const chart = getChartById(id);
        if (action === 'close-panel') {
          document.getElementById('ux-data-panel-' + id)?.classList.remove('open');
          return;
        }
        if (!chart) { toast('Chart is still loading'); return; }
        if (action === 'insight') renderDataPanel(chart, 'insight');
        if (action === 'data') renderDataPanel(chart, 'data');
        if (action === 'csv') downloadCsv(chart);
        if (action === 'png') downloadPng(chart);
        if (action === 'focus' || action === 'expand') openStudio(id);
        return;
      }
      const cmd = e.target.closest('[data-ux-command]')?.dataset.uxCommand;
      if (!cmd) return;
      if (cmd === 'palette') openPalette();
      if (cmd === 'density') toggleDensity();
      if (cmd === 'presentation') togglePresentation();
      if (cmd === 'export-all') exportAllCharts();
    });
    document.addEventListener('keydown', e => {
      const isMac = navigator.platform.toUpperCase().includes('MAC');
      if ((isMac ? e.metaKey : e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        openPalette();
      }
      if (e.key === 'Escape') {
        closePalette();
        closeStudio();
        document.querySelectorAll('.ux-click-popover').forEach(el => el.remove());
      }
    });
  }

  function observeDom() {
    const target = document.getElementById('appRoot') || document.body;
    const observer = new MutationObserver(() => {
      if (observerQueued) return;
      observerQueued = true;
      requestAnimationFrame(() => {
        observerQueued = false;
        enhanceCharts();
        polishAllChartLayouts();
        forceEveryChartExpandable();
        ensureVoiceAiInTeamDropdowns();
      });
    });
    observer.observe(target, { childList: true, subtree: true });
    setInterval(() => { enhanceCharts(); polishAllChartLayouts(); forceEveryChartExpandable(); chartInstances().forEach(scheduleChartResize); }, 1200);
  }


  function removeKnownNoisyUi() {
    // Defensive cleanup for older cached markup or manually merged files. The current CSAT UI is open and Domo-first.
    const forbiddenText = ['Data quality:', 'Protected drilldowns', 'Admin access', 'Unlock raw drilldowns'];
    document.querySelectorAll('button, .card, .alert, .section, [id]').forEach(el => {
      const txt = (el.textContent || '').trim();
      if (forbiddenText.some(term => txt.includes(term))) {
        if (el.id === 'domoRefreshBtn') return;
        el.style.display = 'none';
        el.setAttribute('aria-hidden', 'true');
      }
    });
    document.querySelectorAll('[id*="domoAccess"], [id*="protected"], [id*="Protected"]').forEach(el => {
      el.style.display = 'none';
      el.setAttribute('aria-hidden', 'true');
    });
  }


  function compactTeamName(value) {
    return String(value || '').replace(/[^a-z0-9]/gi, '').toLowerCase();
  }

  function ensureVoiceAiInTeamDropdowns() {
    const likelyTeamSelects = Array.from(document.querySelectorAll('select')).filter(sel => {
      const idName = ((sel.id || '') + ' ' + (sel.name || '') + ' ' + (sel.getAttribute('aria-label') || '')).toLowerCase();
      const prevLabel = sel.closest('label')?.textContent?.toLowerCase() || '';
      return idName.includes('team') || idName.includes('csatglobalteamfilter') || idName.includes('csatteamfilter') || prevLabel.includes('team');
    });
    likelyTeamSelects.forEach(sel => {
      const hasVoiceAi = Array.from(sel.options || []).some(opt => compactTeamName(opt.value || opt.textContent) === 'voiceai');
      if (!hasVoiceAi) {
        const opt = document.createElement('option');
        opt.value = 'Voice AI';
        opt.textContent = 'Voice AI';
        // Keep the global "All teams" option first, then show Voice AI near the top.
        if (sel.options && sel.options.length > 1) sel.insertBefore(opt, sel.options[1]);
        else sel.appendChild(opt);
      }
    });
  }

  function forceEveryChartExpandable() {
    // Defensive guarantee: every Chart.js canvas must have exactly one expand action.
    // Older packages could create duplicated toolbars; this consolidates the action into the primary toolbar.
    document.querySelectorAll('canvas[id]').forEach(canvas => {
      if (isUxInternalCanvas(canvas) || canvas.closest('#uxChartStudio, #chartModal')) return;
      const chart = window.Chart && Chart.getChart(canvas);
      if (!chart) return;
      const allButtons = Array.from(document.querySelectorAll('[data-ux-action="expand"][data-chart-id]'))
        .filter(btn => btn.dataset.chartId === canvas.id);
      if (allButtons.length > 1) allButtons.slice(1).forEach(btn => btn.remove());
      if (allButtons.length === 1) return;
      let bar = Array.from(document.querySelectorAll('.ux-chart-toolbar[data-chart-id]'))
        .find(el => el.dataset.chartId === canvas.id);
      if (!bar) {
        const wrap = canvas.closest('.chart-wrap') || canvas.parentElement;
        if (!wrap) return;
        bar = document.createElement('div');
        bar.className = 'ux-chart-toolbar';
        bar.dataset.chartId = canvas.id;
        bar.innerHTML = '<div class="ux-chart-actions" role="toolbar" aria-label="Chart actions"></div>';
        wrap.insertBefore(bar, wrap.firstChild);
      }
      let actions = bar.querySelector('.ux-chart-actions');
      if (!actions) {
        actions = document.createElement('div');
        actions.className = 'ux-chart-actions';
        actions.setAttribute('role', 'toolbar');
        actions.setAttribute('aria-label', 'Chart actions');
        bar.appendChild(actions);
      }
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ux-chart-action ux-expand-guarantee';
      btn.dataset.uxAction = 'expand';
      btn.dataset.chartId = canvas.id;
      btn.textContent = '⤢';
      btn.setAttribute('aria-label', 'Expand chart');
      btn.setAttribute('title', 'Expand chart');
      actions.appendChild(btn);
    });
  }

  function init() {
    document.body.classList.add('pro-dashboard', 'ux-no-hero');
    removeKnownNoisyUi();
    ensureVoiceAiInTeamDropdowns();
    applyPrefs();
    injectSkipLink();
    injectHero();
    buildPageMap();
    installGlobalHandlers();
    enhanceCharts();
    polishAllChartLayouts();
    forceEveryChartExpandable();
    ensureVoiceAiInTeamDropdowns();
    observeDom();
    setTimeout(() => { enhanceCharts(); polishAllChartLayouts(); forceEveryChartExpandable(); ensureVoiceAiInTeamDropdowns(); recolorAllCharts(); chartInstances().forEach(scheduleChartResize); }, 800);
    setTimeout(() => { enhanceCharts(); polishAllChartLayouts(); forceEveryChartExpandable(); ensureVoiceAiInTeamDropdowns(); recolorAllCharts(); chartInstances().forEach(scheduleChartResize); }, 2200);
    setTimeout(() => { enhanceCharts(); polishAllChartLayouts(); forceEveryChartExpandable(); ensureVoiceAiInTeamDropdowns(); recolorAllCharts(); chartInstances().forEach(scheduleChartResize); }, 4200);
    setInterval(() => { ensureVoiceAiInTeamDropdowns(); enhanceCharts(); polishAllChartLayouts(); forceEveryChartExpandable(); chartInstances().forEach(scheduleChartResize); }, 1500);
    UX.refreshCharts = () => { enhanceCharts(); polishAllChartLayouts(); forceEveryChartExpandable(); };
    console.info('[DashboardUX] World-class UX engine active', UX.version);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();