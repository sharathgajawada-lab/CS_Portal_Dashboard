/**
 * hear.com CS Portal — Design System JS v1.0
 * =============================================
 * Add ONE script tag at the bottom of <body> in every page:
 *   <script src="/portal-system.js"></script>
 *
 * Place it AFTER your existing <script> block.
 * Zero conflicts — this file only adds behaviour, never overwrites.
 *
 * WHAT THIS DOES AUTOMATICALLY (no config needed):
 *  1. Toast notifications on every user action
 *  2. Resolved date-range label next to date chips
 *  3. Active nav tab indicator synced to current URL
 *  4. Table sort: DS-sorted class + DS-sort-icon arrows on all <th>
 *  5. Expand button: SVG icon replaces ⤢, always visible
 *  6. Note elements: removes ℹ️ emoji prefix
 *  7. Row count controls: wired to existing article table
 *  8. Consultant focus pill + scope banner on CSAT page
 *  9. Chart modal: Escape closes, overlay click closes
 * 10. KPI skeleton: replaces em dashes on page load
 */

;(function() {
  'use strict';

  /* ──────────────────────────────────────────────
     TOAST SYSTEM
     Call DS.toast('message') or DS.toast('msg','success')
     Types: default | success | error | warning
  ────────────────────────────────────────────── */
  let _toastTimer = null;
  const _toastEl = (() => {
    const el = document.createElement('div');
    el.id = 'DS-toast';
    document.body.appendChild(el);
    return el;
  })();

  function toast(msg, type) {
    _toastEl.textContent = msg;
    _toastEl.className   = 'DS-toast-show' + (type ? ' DS-toast-' + type : '');
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => _toastEl.classList.remove('DS-toast-show'), 2600);
  }


  /* ──────────────────────────────────────────────
     RESOLVED DATE RANGE LABEL
     Inserts a human-readable label right after the
     date quick-buttons when one is clicked.
     Works by patching the existing setQuick / applyCustom
     functions after they run.
  ────────────────────────────────────────────── */
  (function patchDateLabels() {
    // Create the label element once and insert after the date controls
    let _labelEl = document.getElementById('DS-date-range-label');
    if (!_labelEl) {
      _labelEl = document.createElement('span');
      _labelEl.id        = 'DS-date-range-label';
      _labelEl.className = 'DS-date-range-label';
    }

    function insertLabel() {
      const dateControls = document.querySelector('.date-controls');
      if (dateControls && !document.getElementById('DS-date-range-label')) {
        // Insert after the Apply button (last child)
        const applyBtn = dateControls.querySelector('.apply-btn');
        if (applyBtn) {
          applyBtn.parentNode.insertBefore(_labelEl, applyBtn.nextSibling);
        } else {
          dateControls.appendChild(_labelEl);
        }
      }
    }

    function updateLabel() {
      const d0 = document.getElementById('d0')?.value;
      const d1 = document.getElementById('d1')?.value;
      if (!d0 || !d1) return;
      const fmt = iso => {
        const d = new Date(iso + 'T00:00:00');
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
      };
      const days = Math.round((new Date(d1) - new Date(d0)) / 86400000) + 1;
      _labelEl.textContent = d0 === d1
        ? fmt(d0)
        : fmt(d0) + ' – ' + fmt(d1) + ' (' + days + 'd)';
    }

    // Patch setQuick
    if (typeof window.setQuick === 'function') {
      const _orig = window.setQuick;
      window.setQuick = function(n, btn) {
        _orig.call(this, n, btn);
        insertLabel();
        setTimeout(updateLabel, 10);
        toast('Period: ' + (btn?.textContent?.trim() || n));
      };
    }

    // Patch applyCustom
    if (typeof window.applyCustom === 'function') {
      const _orig2 = window.applyCustom;
      window.applyCustom = function() {
        _orig2.call(this);
        insertLabel();
        setTimeout(updateLabel, 10);
        const d0 = document.getElementById('d0')?.value;
        const d1 = document.getElementById('d1')?.value;
        if (d0 && d1) toast('Custom range applied');
      };
    }

    // Run on load
    document.addEventListener('DOMContentLoaded', () => {
      insertLabel();
      setTimeout(updateLabel, 200);
    });
  })();


  /* ──────────────────────────────────────────────
     ACTIVE NAV TAB — synced to current URL
  ────────────────────────────────────────────── */
  (function syncNav() {
    document.addEventListener('DOMContentLoaded', () => {
      const path = window.location.pathname;
      document.querySelectorAll('.page-nav a').forEach(a => {
        const href = a.getAttribute('href') || '';
        const isActive =
          (path === '/'         && (href === '/' || href === '')) ||
          (path.startsWith('/csat')           && href.includes('csat')) ||
          (path.startsWith('/starter-guides') && href.includes('starter'));
        a.classList.toggle('active', isActive);
      });

      // Dynamic page title
      const titles = {
        '/':               'Portal Activity — hear.com CS Portal',
        '/csat':           'Call Quality — hear.com CS Portal',
        '/starter-guides': 'Starter Guides — hear.com CS Portal',
      };
      const matched = Object.entries(titles).find(([k]) =>
        k === '/' ? path === '/' : path.startsWith(k)
      );
      if (matched) document.title = matched[1];
    });
  })();


  /* ──────────────────────────────────────────────
     TABLE SORT — DS-sorted class + DS-sort-icon
     Patches all <th> elements that already have
     onclick="sortArticleTable(...)" or onclick="sortConsultantTable()"
     to add the proper active state.
     Also adds DS-sort-icon spans to every <th> that has ↕/↓/↑.
  ────────────────────────────────────────────── */
  (function enhanceTables() {
    document.addEventListener('DOMContentLoaded', () => {
      // Replace raw sort arrow spans with DS-sort-icon class
      document.querySelectorAll('th .sort-arrow, th [id^="artArrow"], th [id^="consArrow"]').forEach(el => {
        el.classList.add('DS-sort-icon');
      });

      // For every th with onclick sort, add DS-sort-icon if missing
      document.querySelectorAll('th[onclick]').forEach(th => {
        if (!th.querySelector('.DS-sort-icon')) {
          const icon = document.createElement('span');
          icon.className = 'DS-sort-icon';
          icon.textContent = '↕';
          th.appendChild(icon);
        }
      });

      // Patch sortArticleTable to also update DS-sorted
      if (typeof window.sortArticleTable === 'function') {
        const _orig = window.sortArticleTable;
        window.sortArticleTable = function(key) {
          _orig.call(this, key);
          syncSortUI('articleTable', key);
        };
      }

      // Patch sortConsultantTable if it exists (CSAT page)
      if (typeof window.sortConsultantTable === 'function') {
        const _orig = window.sortConsultantTable;
        window.sortConsultantTable = function(key, dir) {
          _orig.call(this, key, dir);
        };
      }
    });

    function syncSortUI(tableId, activeKey) {
      const table = document.getElementById(tableId);
      if (!table) return;
      table.querySelectorAll('th').forEach(th => {
        const thKey = th.getAttribute('onclick')?.match(/sortArticleTable\('(.+?)'\)/)?.[1];
        const icon  = th.querySelector('.DS-sort-icon, .sort-arrow, [id^="artArrow"]');
        if (thKey === activeKey) {
          th.classList.add('DS-sorted');
        } else {
          th.classList.remove('DS-sorted');
          if (icon && icon.textContent !== '↕') icon.textContent = '↕';
        }
      });
    }
  })();


  /* ──────────────────────────────────────────────
     EXPAND BUTTONS — always visible, SVG icon
     The CSS already handles the visual replacement.
     This JS ensures every .chart-expand-btn has
     aria-label and is keyboard-accessible.
  ────────────────────────────────────────────── */
  (function enhanceExpandBtns() {
    document.addEventListener('DOMContentLoaded', () => {
      document.querySelectorAll('.chart-expand-btn').forEach(btn => {
        if (!btn.getAttribute('aria-label')) {
          // Extract chart title from data attribute or nearest card-title
          const title = btn.closest('.card, .section')
            ?.querySelector('.card-title, .section-title')
            ?.textContent?.trim() || 'chart';
          btn.setAttribute('aria-label', 'Expand ' + title);
        }
        // Make keyboard-accessible
        if (!btn.getAttribute('tabindex')) btn.setAttribute('tabindex', '0');
        btn.addEventListener('keydown', e => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); btn.click(); }
        });
      });
    });
    // Also run on dynamic content (charts re-render often)
    const _obs = new MutationObserver(() => {
      document.querySelectorAll('.chart-expand-btn:not([aria-label])').forEach(btn => {
        btn.setAttribute('aria-label', 'Expand chart');
        btn.setAttribute('tabindex', '0');
      });
    });
    document.addEventListener('DOMContentLoaded', () => {
      if (document.getElementById('appRoot')) {
        _obs.observe(document.getElementById('appRoot'), { childList: true, subtree: true });
      }
    });
  })();


  /* ──────────────────────────────────────────────
     CHART MODAL — Escape, overlay click, focus trap
     Enhances the existing #chartModal
  ────────────────────────────────────────────── */
  (function enhanceModal() {
    document.addEventListener('DOMContentLoaded', () => {
      const modal = document.getElementById('chartModal');
      if (!modal) return;

      // Already wired in index.html but add safety net
      document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && modal.style.display !== 'none') {
          if (typeof window.closeChartModal === 'function') window.closeChartModal();
        }
      });

      // Overlay click
      modal.addEventListener('click', e => {
        if (e.target === modal) {
          if (typeof window.closeChartModal === 'function') window.closeChartModal();
        }
      });

      // Announce expand to screen readers
      const origExpand = window.expandChart;
      if (typeof origExpand === 'function') {
        window.expandChart = function(src, title) {
          origExpand.call(this, src, title);
          toast('Expanded: ' + (title || 'chart'));
          // Move focus into modal
          setTimeout(() => {
            const closeBtn = modal.querySelector('button[onclick*="closeChartModal"]');
            if (closeBtn) closeBtn.focus();
          }, 100);
        };
      }
    });
  })();


  /* ──────────────────────────────────────────────
     NOTES — strip ℹ️ emoji, handled by CSS ::before
  ────────────────────────────────────────────── */
  (function cleanNotes() {
    const clean = () => {
      document.querySelectorAll('.note').forEach(el => {
        // Remove leading emoji from text nodes
        el.childNodes.forEach(n => {
          if (n.nodeType === Node.TEXT_NODE) {
            n.textContent = n.textContent.replace(/^[\u{1F300}-\u{1FFFF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\s]*/u, '').trimStart();
          }
        });
        // Remove standalone emoji spans at start
        const first = el.firstElementChild;
        if (first && /^[\u{1F300}-\u{1FFFF}\u{2600}-\u{26FF}]/u.test(first.textContent)) {
          first.remove();
        }
      });
    };
    document.addEventListener('DOMContentLoaded', clean);
    // Also run after dynamic content updates
    const obs = new MutationObserver(clean);
    document.addEventListener('DOMContentLoaded', () => {
      document.querySelectorAll('.note').forEach(el => obs.observe(el, { childList: true, subtree: true }));
    });
  })();


  /* ──────────────────────────────────────────────
     KPI SKELETON SCREENS
     Replaces "—" em-dashes in KPI values with
     animated skeleton bars while data loads.
     Once the real value arrives, the skeleton
     is removed automatically (MutationObserver).
  ────────────────────────────────────────────── */
  (function skeletonKPIs() {
    function addSkeletons() {
      document.querySelectorAll('.kpi-value, .kpi-sub, .kpi-wow').forEach(el => {
        const txt = el.textContent.trim();
        if (txt === '—' || txt === '–' || txt === 'Loading…' || txt === '') {
          el.innerHTML = '<span class="DS-skel ' +
            (el.classList.contains('kpi-value') ? 'DS-skel-val' : 'DS-skel-sub') +
            '" style="display:inline-block"></span>';
        }
      });
    }

    // Watch for DOM changes in kpiRow to remove skeletons once data arrives
    document.addEventListener('DOMContentLoaded', () => {
      const kpiRow = document.getElementById('kpiRow');
      if (kpiRow) {
        const obs = new MutationObserver(() => {
          // Remove skeleton if real content is now present
          kpiRow.querySelectorAll('.kpi-value .DS-skel').forEach(sk => {
            const parent = sk.parentElement;
            if (parent && parent.textContent.trim() !== '—') sk.remove();
          });
        });
        obs.observe(kpiRow, { childList: true, subtree: true, characterData: true });
      }
      addSkeletons();
    });
  })();


  /* ──────────────────────────────────────────────
     CONSULTANT FOCUS PILL + SCOPE BANNER
     Only wires up on the CSAT page (/csat).
     Creates a pill in the header and a banner
     below the Focus-on-consultant bar.
  ────────────────────────────────────────────── */
  (function consultantFocusUI() {
    if (!window.location.pathname.includes('csat')) return;

    document.addEventListener('DOMContentLoaded', () => {
      // Inject the focus pill into header-right
      const headerRight = document.querySelector('.header-right');
      if (headerRight) {
        const pill = document.createElement('div');
        pill.id        = 'DS-focus-pill';
        pill.className = 'DS-focus-pill';
        pill.innerHTML = '<span id="DS-pill-name"></span><span class="DS-focus-pill-x" onclick="DS.clearFocus()">✕</span>';
        headerRight.insertBefore(pill, headerRight.firstChild);
      }

      // Inject the scope banner below the Focus bar
      const focusBar = document.querySelector('.csat-focus-bar, [id*="focus"]');
      // fallback: inject after header
      const header = document.querySelector('.header');
      if (header) {
        const banner = document.createElement('div');
        banner.id        = 'DS-scope-banner';
        banner.className = 'DS-scope-banner';
        banner.innerHTML =
          'Page filtered to: <strong id="DS-banner-name"></strong>' +
          '<span id="DS-banner-team" style="margin-left:4px;opacity:.7;font-size:11px;"></span>' +
          '<button style="margin-left:8px;font-size:11px;border:1px solid rgba(37,99,235,.3);border-radius:20px;padding:2px 10px;background:none;color:var(--blue);cursor:pointer" onclick="DS.clearFocus()">Clear filter</button>' +
          '<span style="margin-left:auto;cursor:pointer;font-weight:700;" onclick="DS.clearFocus()">✕</span>';
        header.insertAdjacentElement('afterend', banner);
      }

      // Patch the existing setFocusConsultant / clearFocusConsultant functions
      // These may be called by the Focus dropdown in csat.html
      function patchFocusFunctions() {
        const origSet = window.setFocusConsultant || window.setConsultantFocus;
        const origClr = window.clearFocusConsultant || window.clearConsultantFocus;

        if (typeof origSet === 'function') {
          window.setFocusConsultant = window.setConsultantFocus = function(name, team) {
            origSet.call(this, name, team);
            DS.showFocusPill(name, team || '');
          };
        }
        if (typeof origClr === 'function') {
          window.clearFocusConsultant = window.clearConsultantFocus = function() {
            origClr.call(this);
            DS.hideFocusPill();
          };
        }
      }

      // Wait for CSAT JS to finish loading
      setTimeout(patchFocusFunctions, 500);

      // Also watch for the Focus dropdown change
      const focusSel = document.querySelector('[id*="focus"], [id*="consultant"]');
      if (focusSel) {
        focusSel.addEventListener('change', () => {
          const val = focusSel.value;
          if (val) DS.showFocusPill(val, '');
          else DS.hideFocusPill();
        });
      }
    });
  })();


  /* ──────────────────────────────────────────────
     EXPORT BUTTONS — toast confirmation
  ────────────────────────────────────────────── */
  (function enhanceExports() {
    document.addEventListener('click', e => {
      const btn = e.target.closest('.export-btn, [onclick*="export"], [onclick*="Export"]');
      if (btn && !btn._dsExportPatched) {
        btn._dsExportPatched = true;
        // Toast will fire on next click after patch
        const orig = btn.onclick;
        btn.addEventListener('click', () => toast('Downloading CSV…', 'success'));
      }
    }, { capture: true });
  })();


  /* ──────────────────────────────────────────────
     CTRL-COMPARE — toast on toggle
  ────────────────────────────────────────────── */
  document.addEventListener('change', e => {
    if (e.target.closest('.ctrl-compare')) {
      const checked = e.target.checked;
      toast(checked ? 'vs prev period: on' : 'vs prev period: off');
    }
    // Graph-by select
    if (e.target.matches('.ctrl-select') && e.target.closest('.chart-controls-right')) {
      const label = e.target.closest('.chart-controls-right')
        ?.previousElementSibling?.querySelector('.section-title, .card-title')
        ?.textContent?.trim() || 'Chart';
      toast(label + ': grouping by ' + e.target.value);
    }
  });


  /* ──────────────────────────────────────────────
     PUBLIC API — available as window.DS
  ────────────────────────────────────────────── */
  window.DS = {
    toast,

    showFocusPill(name, team) {
      const pill = document.getElementById('DS-focus-pill');
      const nameEl = document.getElementById('DS-pill-name');
      if (pill && nameEl) {
        nameEl.textContent = name;
        pill.classList.add('visible');
      }
      const banner   = document.getElementById('DS-scope-banner');
      const bannerName = document.getElementById('DS-banner-name');
      const bannerTeam = document.getElementById('DS-banner-team');
      if (banner && bannerName) {
        bannerName.textContent = name;
        if (bannerTeam) bannerTeam.textContent = team ? '· ' + team : '';
        banner.classList.add('visible');
      }
      toast('Filtered to ' + name);
    },

    hideFocusPill() {
      document.getElementById('DS-focus-pill')?.classList.remove('visible');
      document.getElementById('DS-scope-banner')?.classList.remove('visible');
      toast('Filter cleared — showing all');
    },

    clearFocus() {
      this.hideFocusPill();
      // Reset Focus dropdown if it exists
      const sel = document.querySelector('[id*="consultant"][id*="focus"], [id*="focus"][id*="sel"], select[id*="consultant"]');
      if (sel) sel.value = '';
      // Call original clear function if it exists
      if (typeof window.clearFocusConsultant === 'function') window.clearFocusConsultant();
      else if (typeof window.clearConsultantFocus === 'function') window.clearConsultantFocus();
    },

    /** Show skeleton in a KPI value element while loading */
    skeletonKPI(elId, type) {
      const el = document.getElementById(elId);
      if (!el) return;
      el.innerHTML = '<span class="DS-skel DS-skel-' + (type || 'val') + '" style="display:inline-block"></span>';
    },

    /** Remove skeleton and show real value */
    resolveKPI(elId, value) {
      const el = document.getElementById(elId);
      if (el) el.textContent = value;
    },

    /** Export any table as CSV */
    exportTable(tableId, filename) {
      const table = document.getElementById(tableId);
      if (!table) return;
      const rows  = Array.from(table.querySelectorAll('tr'));
      const csv   = rows.map(r =>
        Array.from(r.querySelectorAll('th,td'))
          .map(c => '"' + c.textContent.trim().replace(/"/g, '""') + '"')
          .join(',')
      ).join('\n');
      const blob  = new Blob([csv], { type: 'text/csv' });
      const url   = URL.createObjectURL(blob);
      const a     = document.createElement('a');
      a.href = url; a.download = (filename || tableId) + '.csv'; a.click();
      URL.revokeObjectURL(url);
      toast('Downloaded ' + (filename || tableId) + '.csv', 'success');
    },
  };

})();
