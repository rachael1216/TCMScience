/* ==========================================================================
 * TCMScience Arena — application script.
 *
 * READ-ONLY (ADR-0004). This file renders published result bundles. It never
 * computes a score, never re-ranks, and never writes anything. Every number
 * on screen — dimension scores, raw latency and cost, the aggregate, the rank
 * and the board — is read from JSON as published by the result generator.
 * Where this script sorts or filters, it is changing what you are looking at,
 * not what the number is.
 *
 * No framework, no build step, no CDN. Loaded as a classic script.
 * ========================================================================== */

(function () {
  'use strict';

  /* ----------------------------------------------------------------------
   * Constants
   * -------------------------------------------------------------------- */

  var DATA_NAMES = ['tracks', 'leaderboard', 'runs', 'benchmarks', 'skills'];

  /* Stable strings from bioagent.contracts. Published here so a reader can
   * look up a refusal code without leaving the page. Never renumbered. */
  var CODE_MEANINGS = {
    ART101: 'artifact declares no sources',
    ART102: 'a publishable claim rests on a source that is not pinned to a snapshot',
    ART103: 'a publishable claim rests on a source with no identified licence',
    ART104: 'a claim cites evidence id that is not present in the artifact',
    ART105: 'a claim is not supported by its evidence',
    ART106: 'a clinical claim is supported only by computational prediction',
    ART107: 'a declared output file has no content hash',
    ART108: 'composite_version is incomplete',
    ART109: 'a claim cites retracted evidence',
    ART110: 'no limitations are stated',
    ART111: 'an evidence item names a source card that is not in the artifact',
    ART112: 'a claim declares a confidence basis but evidence quality is unassessed',
    CLM001: 'claim cites no evidence',
    CLM002: 'a cited evidence item is not present',
    CLM003: 'all supporting evidence is retracted',
    CLM004: 'a clinical claim rests only on computational prediction',
    CLM005: 'evidence tier is below the floor for this claim kind',
    CLM006: 'evidence quality blocks this claim',
    CLM007: 'a normative claim does not use claim_kind ‘recommendation’',
    CLM008: 'cross-species extrapolation is not declared',
    CLM009: 'an extrapolation beyond the evidence is not declared',
    CLM010: 'a supporting quote was not located in its source',
    SAF002: 'a severe safety signal was missed in a case the gold set flags'
  };

  /* The prediction prohibition, in one line, used where a run trips it. */
  var PREDICTION_RULE =
    'Computational prediction (docking, network inference, target prediction, ' +
    'pathway enrichment) may support a mechanism claim. It may never support ' +
    'a clinical one.';

  var TYPE_GLYPH = { 'Skill': 'S', 'OCI Container': 'C', 'Remote API': 'R' };
  /* A `?` rather than `undefined` for a board this build does not know: a
   * generator may add one, and rendering the string "undefined" into the label
   * is how that shows up. */
  var BOARD_GLYPH = { trusted: '✓', experimental: '⚠', demonstration: '◆', all: '·' };
  var BOARD_FALLBACK_GLYPH = '·';

  /* ----------------------------------------------------------------------
   * Small helpers
   * -------------------------------------------------------------------- */

  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function $(selector, root) { return (root || document).querySelector(selector); }
  function $$(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  function getParam(name) {
    var match = new RegExp('[?&]' + name + '=([^&#]*)').exec(window.location.search);
    return match ? decodeURIComponent(match[1].replace(/\+/g, ' ')) : null;
  }

  function setParams(params) {
    /* replaceState throws on file:// in some browsers. The page must still
     * work from a double-clicked file, so failure here is not an error. */
    try {
      var query = Object.keys(params)
        .filter(function (k) { return params[k] !== null && params[k] !== '' && params[k] !== undefined; })
        .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); })
        .join('&');
      window.history.replaceState(null, '', window.location.pathname + (query ? '?' + query : ''));
    } catch (err) { /* file:// — filters simply do not persist in the URL */ }
  }

  function isNum(value) {
    return value !== null && value !== undefined && value !== '' && isFinite(Number(value));
  }

  /* formatScore — the only formatting entry point for a published score.
   * It formats; it never derives. A null score stays visibly null. */
  function formatScore(value, opts) {
    opts = opts || {};
    if (!isNum(value)) return opts.dash || '—';
    var n = Number(value);
    if (opts.percent) return (n * 100).toFixed(opts.digits === undefined ? 1 : opts.digits) + '%';
    return n.toFixed(opts.digits === undefined ? 3 : opts.digits);
  }

  function formatCompact(value) {
    if (!isNum(value)) return '—';
    var n = Number(value);
    if (Math.abs(n) >= 1000) return n.toLocaleString('en-US');
    return String(n);
  }

  function formatDelta(value) {
    if (!isNum(value)) return 'not measured';
    var n = Number(value);
    return (n > 0 ? '+' : n < 0 ? '−' : '±') + Math.abs(n).toFixed(3);
  }

  function formatDate(value) {
    if (!value) return '—';
    var text = String(value);
    var date = new Date(text);
    if (isNaN(date.getTime())) return text;
    return date.toISOString().slice(0, 10);
  }

  function formatDateTime(value) {
    if (!value) return '—';
    var date = new Date(String(value));
    if (isNaN(date.getTime())) return String(value);
    return date.toISOString().replace('T', ' ').replace(/:\d\d(\.\d+)?Z$/, ' UTC');
  }

  function titleCase(text) {
    return String(text || '').replace(/(^|[\s_-])([a-z])/g, function (m, a, b) {
      return (a === '_' || a === '-' ? ' ' : a) + b.toUpperCase();
    });
  }

  function codeChip(code, extraClass) {
    var meaning = CODE_MEANINGS[code];
    return '<span class="chip chip--code ' + (extraClass || '') + '" title="' +
      esc(meaning || 'code not in the published table') + '">' + esc(code) + '</span>';
  }

  /* ----------------------------------------------------------------------
   * Data loading
   *
   * Order: generated JSON -> example JSON -> embedded fallback.
   *
   * The first two are plain `fetch`, which is what the server build uses and
   * what the data contract describes. `fetch` of a `file://` URL is blocked
   * as cross-origin by every current browser, so a double-clicked page falls
   * through to `assets/fallback-data.js`, a byte-for-byte mirror of the
   * example JSON loaded by a classic <script> tag.
   * -------------------------------------------------------------------- */

  var loadState = { example: false, sources: {} };

  function loadJSON(name) {
    var candidates = ['data/' + name + '.json', 'data/' + name + '.example.json'];

    function attempt(index) {
      if (index >= candidates.length) {
        var embedded = window.ARENA_FALLBACK && window.ARENA_FALLBACK[name];
        if (embedded) {
          loadState.example = true;
          loadState.sources[name] = 'assets/fallback-data.js';
          return Promise.resolve(embedded);
        }
        return Promise.reject(new Error('No data for "' + name + '" (looked for ' +
          candidates.join(', ') + ' and the embedded fallback).'));
      }
      var url = candidates[index];
      return fetch(url, { cache: 'no-store' }).then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      }).then(function (doc) {
        if (url.indexOf('.example.') !== -1) loadState.example = true;
        loadState.sources[name] = url;
        return doc;
      }).catch(function () { return attempt(index + 1); });
    }

    return attempt(0);
  }

  function loadMany(names) {
    return Promise.all(names.map(function (name) {
      return loadJSON(name).then(function (doc) { return [name, doc]; });
    })).then(function (pairs) {
      var out = {};
      pairs.forEach(function (pair) { out[pair[0]] = pair[1]; });
      return out;
    });
  }

  /* ----------------------------------------------------------------------
   * Shared chrome
   * -------------------------------------------------------------------- */

  function renderDataBanner() {
    var host = $('#data-banner');
    if (!host || !loadState.example) return;
    var used = Object.keys(loadState.sources).map(function (name) {
      return name + ' ← ' + loadState.sources[name];
    });
    host.innerHTML =
      '<div class="wrap data-banner__inner">' +
      '<span><strong>Example data.</strong> The generated result files are not present, so this page ' +
      'is rendering <code>*.example.json</code>. Every row is a placeholder and every number is ' +
      'synthetic. The layout, the decomposition and the gate logic are the real ones.</span>' +
      '<span class="small mono">' + esc(used.join('  ·  ')) + '</span>' +
      '</div>';
    host.hidden = false;
  }

  function renderError(host, error, hint) {
    if (!host) return;
    host.innerHTML =
      '<div class="callout callout--danger">' +
      '<p class="callout__title">This page could not load its data.</p>' +
      '<p class="mono small">' + esc(error && error.message ? error.message : String(error)) + '</p>' +
      (hint ? '<p class="small">' + hint + '</p>' : '') +
      '</div>';
  }

  function boardBadge(board, label) {
    var isTrusted = board === 'trusted';
    return '<span class="badge ' + (isTrusted ? 'badge--ok' : 'badge--warn') + '">' +
      '<span class="badge__glyph" aria-hidden="true">' + (BOARD_GLYPH[board] || BOARD_FALLBACK_GLYPH) + '</span>' +
      esc(label || (isTrusted ? 'Trusted' : 'Experimental')) + '</span>';
  }

  function typeBadge(type) {
    return '<span class="badge badge--plain" title="Submission type: ' + esc(type) + '">' +
      '<span class="badge__glyph" aria-hidden="true">' + esc(TYPE_GLYPH[type] || '?') + '</span>' +
      esc(type) + '</span>';
  }

  /* A score cell: the number is the information, the bar is a reading aid. */
  function meterCell(value, opts) {
    opts = opts || {};
    var has = isNum(value);
    var html = '<div class="meter' + (opts.aggregate ? ' meter--aggregate' : '') + '">';
    html += '<span class="meter__value' + (has ? '' : ' meter__value--null') + '">' +
      formatScore(value, { dash: '—' }) + '</span>';
    if (opts.ci !== undefined && isNum(opts.ci)) {
      html += ' <span class="meter__ci">±' + formatScore(opts.ci, { digits: 3 }) + '</span>';
    }
    if (has) {
      html += '<span class="meter__bar" aria-hidden="true"><span class="meter__fill" style="width:' +
        Math.max(0, Math.min(100, Number(value) * 100)).toFixed(1) + '%"></span></span>';
    }
    if (opts.raw) html += '<span class="meter__raw">' + esc(opts.raw) + '</span>';
    return html + '</div>';
  }

  function rawLabel(key, raw) {
    if (!raw) return '';
    if (key === 'latency' && isNum(raw.latency_s)) return formatCompact(raw.latency_s) + ' s';
    if (key === 'cost' && isNum(raw.cost_usd)) return '$' + Number(raw.cost_usd).toFixed(2);
    return '';
  }

  function versionAxes(versions) {
    versions = versions || {};
    var axes = [
      ['Runtime', 'runtime', 'the kernel that executed the run'],
      ['Skill', 'skill', 'the Stable Registry revision it resolved skills from'],
      ['Source', 'source', 'the pinned snapshots of the external databases it read'],
      ['Benchmark', 'benchmark', 'the frozen Season it was scored against']
    ];
    return '<dl class="kv kv--mono">' + axes.map(function (axis) {
      var value = versions[axis[1]];
      return '<dt title="' + esc(axis[2]) + '">' + esc(axis[0]) + '</dt><dd>' +
        (value ? esc(value) : '<span class="muted">not declared — ART108</span>') + '</dd>';
    }).join('') + '</dl>';
  }

  function dimsFrom(tracksDoc, leaderboardDoc) {
    if (tracksDoc && tracksDoc.dimensions) return tracksDoc.dimensions;
    if (leaderboardDoc && leaderboardDoc.dimensions) return leaderboardDoc.dimensions;
    return [];
  }

  function gatesFrom(tracksDoc) {
    return (tracksDoc && tracksDoc.gates) || [];
  }

  /* ----------------------------------------------------------------------
   * Overview (index.html)
   * -------------------------------------------------------------------- */

  function renderOverview() {
    var host = $('#overview');
    if (!host) return;
    loadMany(['tracks', 'leaderboard', 'benchmarks']).then(function (data) {
      var tracks = data.tracks.tracks || [];
      var dims = dimsFrom(data.tracks, data.leaderboard);
      var rows = data.leaderboard.runs || [];
      var cases = tracks.reduce(function (sum, t) { return sum + (t.cases || 0); }, 0);
      var trusted = rows.filter(function (r) { return r.board === 'trusted'; }).length;
      var experimental = rows.length - trusted;
      var systems = {};
      rows.forEach(function (r) { systems[r.system_slug || r.system] = true; });
      var season = data.tracks.season_label || data.tracks.season || '—';

      $('#at-a-glance').innerHTML = [
        ['Tracks', tracks.length, 'each with ' + (tracks[0] ? tracks[0].cases : 20) + ' cases'],
        ['Cases', cases, 'in ' + season + ', frozen'],
        ['Dimensions', dims.length, 'every score is decomposed'],
        ['Systems', Object.keys(systems).length, 'in the example set'],
        ['Rows', rows.length, trusted + ' trusted · ' + experimental + ' experimental']
      ].map(function (item) {
        return '<p class="stat"><span class="stat__value">' + esc(item[1]) + '</span>' +
          '<span class="stat__label">' + esc(item[0]) + '</span>' +
          '<span class="stat__note">' + esc(item[2]) + '</span></p>';
      }).join('');

      $('#overview-tracks').innerHTML = tracks.map(function (track) {
        return '<tr><th scope="row"><a href="leaderboard.html?track=' +
          encodeURIComponent(track.id) + '">' + esc(track.id) + '</a></th>' +
          '<td>' + (track.metric_focus || []).map(function (m) {
            return '<span class="chip">' + esc(m) + '</span>';
          }).join(' ') + '</td>' +
          '<td class="col-num">' + esc(track.cases) + '</td></tr>';
      }).join('');

      $('#overview-gates').innerHTML = gatesFrom(data.tracks).map(function (gate) {
        return '<div class="gate"><div class="gate__head">' +
          '<span class="gate__id">' + esc(gate.id) + '</span>' +
          '<span class="gate__name">' + esc(gate.name) + '</span>' +
          '<span class="gate__rule">' + esc(gate.rule) + '</span></div>' +
          '<p class="gate__text">' + esc(gate.rationale) + '</p></div>';
      }).join('');

      renderDataBanner();
    }).catch(function (error) {
      renderError(host, error, 'Generate the result JSON, or keep the committed example files next to the page.');
    });
  }

  /* ----------------------------------------------------------------------
   * Leaderboard
   * -------------------------------------------------------------------- */

  var LEADERBOARD_STATE = {
    track: 'TCM-Entity',
    type: 'all',
    board: 'trusted',
    query: '',
    sortKey: 'rank',
    sortDir: 'asc'
  };

  /* applyFilters — pure selection over already-published rows. */
  function applyFilters(rows, filters) {
    var needle = (filters.query || '').trim().toLowerCase();
    return rows.filter(function (row) {
      if (filters.board && row.board !== filters.board) return false;
      if (filters.track && filters.track !== 'all' && row.track !== filters.track) return false;
      if (filters.type && filters.type !== 'all' && row.type !== filters.type) return false;
      if (needle) {
        var haystack = [row.system, row.track, row.type, row.version, row.run_id]
          .join(' ').toLowerCase();
        if (haystack.indexOf(needle) === -1) return false;
      }
      return true;
    });
  }

  /* sortRows — a reading aid. The published `rank` field is never rewritten. */
  function sortRows(rows, key, dir) {
    var sign = dir === 'desc' ? -1 : 1;
    function pick(row) {
      if (key === 'system') return row.system;
      if (key === 'rank') return row.rank;
      if (key === 'aggregate') return row.aggregate;
      return row.scores ? row.scores[key] : null;
    }
    return rows.slice().sort(function (a, b) {
      var left = pick(a);
      var right = pick(b);
      if (typeof left === 'string' || typeof right === 'string') {
        return sign * String(left).localeCompare(String(right));
      }
      var l = isNum(left) ? Number(left) : -Infinity;
      var r = isNum(right) ? Number(right) : -Infinity;
      if (l === r) return String(a.run_id).localeCompare(String(b.run_id));
      return sign * (l < r ? -1 : 1);
    });
  }

  function readLeaderboardState() {
    var track = getParam('track'); if (track) LEADERBOARD_STATE.track = track;
    var type = getParam('type'); if (type) LEADERBOARD_STATE.type = type;
    var board = getParam('board'); if (board) LEADERBOARD_STATE.board = board;
    var q = getParam('q'); if (q) LEADERBOARD_STATE.query = q;
    var sort = getParam('sort');
    if (sort) {
      var parts = sort.split(':');
      LEADERBOARD_STATE.sortKey = parts[0];
      if (parts[1]) LEADERBOARD_STATE.sortDir = parts[1];
    }
  }

  /* Column labels, both languages. The header row is built by script, so the
   * `data-en`/`data-zh` trick used in the HTML does not reach it — the labels
   * have to be in the script too, or the table stays English while the rest of
   * the page switches. */
  var DIM_LABELS = {
    en: { task_success: 'Task Success', evidence_grounding: 'Evidence Grounding',
          provenance_completeness: 'Provenance Completeness',
          reproducibility: 'Reproducibility', safety_abstention: 'Safety and Abstention',
          claim_calibration: 'Claim Calibration', latency: 'Latency', cost: 'Cost',
          aggregate: 'Aggregate', rank: 'Rank', system: 'System', type: 'Type' },
    zh: { task_success: '任务成功率', evidence_grounding: '证据锚定',
          provenance_completeness: '溯源完整性', reproducibility: '可复现性',
          safety_abstention: '安全与弃权', claim_calibration: '主张校准',
          latency: '延迟', cost: '成本', aggregate: '综合', rank: '排名',
          system: '系统', type: '类型' }
  };

  function dimLabel(key) {
    var lang = document.documentElement.getAttribute('data-lang') === 'zh' ? 'zh' : 'en';
    return DIM_LABELS[lang][key] || key;
  }

  function renderLeaderboard() {
    var host = $('#leaderboard');
    if (!host) return;

    loadMany(['tracks', 'leaderboard']).then(function (data) {
      var tracksDoc = data.tracks;
      var doc = data.leaderboard;
      var dims = dimsFrom(tracksDoc, doc);
      var gates = gatesFrom(tracksDoc);
      var allRows = doc.runs || [];
      var trackList = (tracksDoc.tracks || []).map(function (t) { return t.id; });

      readLeaderboardState();
      if (LEADERBOARD_STATE.track !== 'all' && trackList.indexOf(LEADERBOARD_STATE.track) === -1) {
        LEADERBOARD_STATE.track = trackList[0] || 'all';
      }

      /* --- filter controls ------------------------------------------- */
      $('#filter-track').innerHTML =
        '<option value="all">All tracks</option>' + trackList.map(function (id) {
          return '<option value="' + esc(id) + '">' + esc(id) + '</option>';
        }).join('');
      $('#filter-track').value = LEADERBOARD_STATE.track;

      var types = [];
      allRows.forEach(function (r) { if (types.indexOf(r.type) === -1) types.push(r.type); });
      types.sort();
      $('#filter-type').innerHTML = '<option value="all">All types</option>' + types.map(function (t) {
        return '<option value="' + esc(t) + '">' + esc(t) + '</option>';
      }).join('');
      $('#filter-type').value = LEADERBOARD_STATE.type;

      $('#filter-query').value = LEADERBOARD_STATE.query;

      /* --- header ------------------------------------------------------ */
      /* Rendered from the Season's dimension list, so the columns and the
       * scores they hold cannot drift apart. */
      function headerCell(label, key, extraClass, hint) {
        var inner = key
          ? '<button class="sort-btn" type="button">' + esc(label) +
            '<span class="sort-btn__glyph" aria-hidden="true">↕</span></button>'
          : esc(label);
        return '<th scope="col" class="' + (extraClass || '') + '"' +
          (key ? ' data-sort-key="' + esc(key) + '"' : '') +
          (hint ? ' title="' + esc(hint) + '"' : '') + '>' + inner + '</th>';
      }
      /* Labels come from `dimLabel`, not from the English strings on the data
       * file, so the table header follows the language switch. */
      var dimLabels = { en: {}, zh: {} };
      dims.forEach(function (dim) {
        dimLabels.en[dim.key] = dim.label;
        dimLabels.zh[dim.key] = dim.label_zh || DIM_LABELS.zh[dim.key] || dim.label;
      });
      function labelFor(key, fallback) {
        var lang = document.documentElement.getAttribute('data-lang') === 'zh' ? 'zh' : 'en';
        return dimLabels[lang][key] || fallback;
      }
      $('#leaderboard-head').innerHTML = '<tr>' +
        headerCell(dimLabel('rank'), 'rank', 'col-num', 'Published rank within this track and board') +
        headerCell(dimLabel('system'), 'system', 'cell-system') +
        headerCell(dimLabel('type'), null, '', 'Submission type: Skill, OCI Container or Remote API') +
        dims.map(function (dim) {
          return headerCell(labelFor(dim.key, dim.label), dim.key, 'col-num',
                            dim.definition || '');
        }).join('') +
        headerCell(dimLabel('aggregate'), 'aggregate', 'col-num',
          'Weighted harmonic mean of the eight dimensions, as published by the generator') +
        '</tr>';

      var footCell = $('#leaderboard-table tfoot td');
      if (footCell) footCell.colSpan = dims.length + 4;

      var boards = (doc.boards || [{ id: 'trusted', label: 'Trusted board' },
                                   { id: 'experimental', label: 'Experimental board' }]);
      $('#filter-board').innerHTML = boards.map(function (board, index) {
        return '<label class="filters__radio"><input type="radio" name="board" value="' +
          esc(board.id) + '"' + (board.id === LEADERBOARD_STATE.board ? ' checked' : '') + '>' +
          '<span aria-hidden="true">' + (BOARD_GLYPH[board.id] || BOARD_FALLBACK_GLYPH) + '</span> ' + esc(board.label) + '</label>';
      }).join('');

      /* --- render ----------------------------------------------------- */
      function draw() {
        var rows = applyFilters(allRows, LEADERBOARD_STATE);
        rows = sortRows(rows, LEADERBOARD_STATE.sortKey, LEADERBOARD_STATE.sortDir);

        var boardMeta = boards.filter(function (b) { return b.id === LEADERBOARD_STATE.board; })[0] || {};
        var trackLabel = LEADERBOARD_STATE.track === 'all' ? 'All tracks' : LEADERBOARD_STATE.track;
        var blocked = rows.filter(function (r) { return (r.blocking_reasons || []).length; });

        $('#leaderboard-caption').innerHTML =
          '<strong>' + esc(trackLabel) + ' · ' + esc(boardMeta.label || LEADERBOARD_STATE.board) + '</strong>' +
          ' — ' + rows.length + ' of ' + allRows.length + ' published rows. ' +
          esc(boardMeta.note || '') +
          ' Rank is published per track and per board by the result generator; sorting a column ' +
          'reorders the view and does not change a published rank.';

        $('#filter-count').textContent = rows.length + ' row' + (rows.length === 1 ? '' : 's') + ' shown';

        if (!rows.length) {
          $('#leaderboard-body').innerHTML =
            '<tr><td colspan="' + (dims.length + 4) + '">' +
            '<div class="empty-state"><h3>No rows match these filters.</h3>' +
            '<p>Nothing has been dropped from the registry — the filters simply select nothing. ' +
            'Try another track or switch board.</p>' +
            '<button class="btn btn--ghost" type="button" id="reset-filters">Reset filters</button>' +
            '</div></td></tr>';
          bindReset();
          return;
        }

        $('#leaderboard-body').innerHTML = rows.map(function (row) {
          var isBlocked = (row.blocking_reasons || []).length > 0;
          var cells = dims.map(function (dim) {
            return '<td class="col-num">' +
              meterCell(row.scores ? row.scores[dim.key] : null, { raw: rawLabel(dim.key, row.raw) }) +
              '</td>';
          }).join('');

          return '<tr class="' + (isBlocked ? 'is-blocked' : '') + '">' +
            '<td class="col-num"><span class="rank' + (isNum(row.rank) ? '' : ' rank--none') + '">' +
              (isNum(row.rank) ? '#' + esc(row.rank) : '—') + '</span></td>' +
            '<td class="cell-system">' +
              '<a class="row-link" href="run.html?id=' + encodeURIComponent(row.run_id) + '">' +
                '<span class="cell-system__name">' + esc(row.system) + '</span></a>' +
              '<div class="cell-system__meta">' + esc(row.track) + ' · v' + esc(row.version) +
                ' · ' + esc(row.cases) + ' cases</div>' +
              flagsFor(row) +
            '</td>' +
            '<td>' + typeBadge(row.type) + '</td>' +
            cells +
            '<td class="col-num">' + meterCell(row.aggregate,
              { aggregate: true, ci: row.aggregate_ci95 }) + '</td>' +
            '</tr>';
        }).join('');

        drawBlockedNotes(blocked, gates);
        markSortHeaders();
        bindRowClicks();
        bindReset();
      }

      function flagsFor(row) {
        var flags = [];
        if (row.placeholder) {
          flags.push('<span class="badge badge--plain" title="Placeholder row from the example data set">example</span>');
        }
        (row.blocking_reasons || []).forEach(function (reason) {
          flags.push('<span class="badge badge--danger" title="' + esc(reason.detail) + '">' +
            '<span class="badge__glyph" aria-hidden="true">✕</span>Blocked · ' +
            esc(reason.gate) + ' ' + esc(gateName(reason.gate, gates)) + '</span>');
        });
        (row.refusal_codes || []).forEach(function (code) {
          flags.push(codeChip(code, 'chip--danger'));
        });
        return flags.length ? '<div class="cell-system__flags">' + flags.join('') + '</div>' : '';
      }

      function drawBlockedNotes(blocked, gates) {
        var hostNode = $('#blocked-notes');
        if (!hostNode) return;
        if (LEADERBOARD_STATE.board !== 'experimental' || !blocked.length) {
          hostNode.innerHTML = '';
          return;
        }
        var seen = {};
        var items = [];
        blocked.forEach(function (row) {
          (row.blocking_reasons || []).forEach(function (reason) {
            var key = row.system + '|' + reason.gate;
            if (seen[key]) return;
            seen[key] = true;
            items.push({ row: row, reason: reason });
          });
        });
        hostNode.innerHTML =
          '<div class="callout callout--warn">' +
          '<p class="callout__title"><span aria-hidden="true">⚠</span> Why these systems are on ' +
          'the Experimental board</p>' +
          '<p class="small">A hard gate is not a score contribution: it cannot be traded against a ' +
          'high aggregate. A blocked run is shown here, labelled, with its blocking reason — ' +
          'never silently dropped.</p>' +
          '<ul>' + items.map(function (item) {
            return '<li><strong><a href="run.html?id=' + encodeURIComponent(item.row.run_id) + '">' +
              esc(item.row.system) + '</a></strong> — ' +
              esc(item.reason.gate) + ' ' + esc(gateName(item.reason.gate, gates)) + ': ' +
              esc(item.reason.detail) + ' ' +
              (item.reason.codes || []).map(function (code) { return codeChip(code, 'chip--danger'); }).join(' ') +
              '</li>';
          }).join('') + '</ul></div>';
      }

      function markSortHeaders() {
        $$('#leaderboard-table thead th').forEach(function (th) {
          var key = th.getAttribute('data-sort-key');
          if (!key) return;
          if (key === LEADERBOARD_STATE.sortKey) {
            th.setAttribute('aria-sort', LEADERBOARD_STATE.sortDir === 'asc' ? 'ascending' : 'descending');
          } else {
            th.removeAttribute('aria-sort');
          }
          var glyph = $('.sort-btn__glyph', th);
          if (glyph) {
            glyph.textContent = key === LEADERBOARD_STATE.sortKey
              ? (LEADERBOARD_STATE.sortDir === 'asc' ? '▲' : '▼') : '↕';
          }
        });
      }

      function bindRowClicks() {
        $$('#leaderboard-body tr').forEach(function (tr) {
          var link = $('a.row-link', tr);
          if (!link) return;
          tr.style.cursor = 'pointer';
          tr.addEventListener('click', function (event) {
            if (event.target.closest('a, button, input, select')) return;
            window.location.href = link.getAttribute('href');
          });
        });
      }

      function bindReset() {
        var button = $('#reset-filters');
        if (!button) return;
        button.addEventListener('click', function () {
          LEADERBOARD_STATE.track = 'TCM-Entity';
          LEADERBOARD_STATE.type = 'all';
          LEADERBOARD_STATE.query = '';
          LEADERBOARD_STATE.board = 'trusted';
          LEADERBOARD_STATE.sortKey = 'rank';
          LEADERBOARD_STATE.sortDir = 'asc';
          $('#filter-track').value = 'TCM-Entity';
          $('#filter-type').value = 'all';
          $('#filter-query').value = '';
          $$('#filter-board input').forEach(function (input) {
            input.checked = input.value === 'trusted';
          });
          syncAndDraw();
        });
      }

      function syncAndDraw() {
        setParams({
          track: LEADERBOARD_STATE.track, type: LEADERBOARD_STATE.type,
          board: LEADERBOARD_STATE.board, q: LEADERBOARD_STATE.query,
          sort: LEADERBOARD_STATE.sortKey + ':' + LEADERBOARD_STATE.sortDir
        });
        draw();
      }

      /* --- events ----------------------------------------------------- */
      $('#filter-track').addEventListener('change', function (e) {
        LEADERBOARD_STATE.track = e.target.value; syncAndDraw();
      });
      $('#filter-type').addEventListener('change', function (e) {
        LEADERBOARD_STATE.type = e.target.value; syncAndDraw();
      });
      $('#filter-query').addEventListener('input', function (e) {
        LEADERBOARD_STATE.query = e.target.value; syncAndDraw();
      });
      $('#filter-board').addEventListener('change', function (e) {
        if (e.target.name === 'board') { LEADERBOARD_STATE.board = e.target.value; syncAndDraw(); }
      });
      $$('#leaderboard-table thead th[data-sort-key]').forEach(function (th) {
        var button = $('.sort-btn', th);
        if (!button) return;
        button.addEventListener('click', function () {
          var key = th.getAttribute('data-sort-key');
          if (LEADERBOARD_STATE.sortKey === key) {
            LEADERBOARD_STATE.sortDir = LEADERBOARD_STATE.sortDir === 'asc' ? 'desc' : 'asc';
          } else {
            LEADERBOARD_STATE.sortKey = key;
            /* A rank or a name reads ascending first; a score reads descending. */
            LEADERBOARD_STATE.sortDir = (key === 'rank' || key === 'system') ? 'asc' : 'desc';
          }
          syncAndDraw();
        });
      });

      draw();
      renderDataBanner();
    }).catch(function (error) { renderError(host, error); });
  }

  function gateName(id, gates) {
    var match = (gates || []).filter(function (g) { return g.id === id; })[0];
    return match ? match.name : '';
  }

  /* ----------------------------------------------------------------------
   * Run detail
   * -------------------------------------------------------------------- */

  function renderRun() {
    var host = $('#run');
    if (!host) return;

    loadMany(['tracks', 'leaderboard', 'runs']).then(function (data) {
      var dims = dimsFrom(data.tracks, data.leaderboard);
      var gates = gatesFrom(data.tracks);
      var runId = getParam('id');
      var published = (data.runs.runs || []);
      var detail = published.filter(function (r) { return r.run_id === runId; })[0];
      var row = (data.leaderboard.runs || []).filter(function (r) { return r.run_id === runId; })[0];

      renderDataBanner();

      if (!runId) {
        host.innerHTML = runPicker(published, 'No run id given.',
          'A run is addressed by its id, for example <code>run.html?id=' +
          esc((published[0] || {}).run_id || 'r-...') + '</code>. Pick one below, or click a row on the leaderboard.');
        return;
      }
      if (!detail && !row) {
        host.innerHTML = runPicker(published, 'Unknown run id: ' + runId,
          'No run with that id appears in the published leaderboard or in the run bundle. ' +
          'Ids are never reused, so this is a typo or a run that was never published.');
        return;
      }

      var record = detail || row;
      var scores = record.scores || {};
      var blocked = (record.blocking_reasons || []).length > 0;

      var html = '';

      /* header */
      html += '<section class="section"><div class="card">' +
        '<div class="card__head">' +
          '<h2 class="mb-0">' + esc(record.system) + '</h2>' +
          typeBadge(record.type) +
          boardBadge(record.board) +
          (isNum(record.rank) ? '<span class="badge badge--plain">Rank #' + esc(record.rank) +
            ' of ' + esc(record.track) + '</span>' : '') +
          (record.artifact_status ? '<span class="badge badge--plain">artifact: ' +
            esc(record.artifact_status) + '</span>' : '') +
          (record.placeholder ? '<span class="badge badge--plain">example</span>' : '') +
        '</div>' +
        '<div class="card__body">' +
          '<dl class="kv">' +
            '<dt>Run id</dt><dd class="mono">' + esc(record.run_id) + '</dd>' +
            '<dt>Track</dt><dd>' + esc(record.track) + ' · ' + esc(record.cases || 20) +
              ' cases · Season ' + esc(data.tracks.season || '—') + '</dd>' +
            '<dt>Submitted</dt><dd>' + esc(formatDateTime(record.submitted_at)) +
              (record.submitted_by ? ' by ' + esc(record.submitted_by) : '') + '</dd>' +
            '<dt>Artifact status</dt><dd>' + esc(record.artifact_status || '—') + '</dd>' +
          '</dl>' +
        '</div>' +
      '</div></section>';

      /* gates */
      if (blocked) {
        html += '<section class="section"><div class="callout callout--danger">' +
          '<p class="callout__title"><span aria-hidden="true">✕</span> Blocked from the trusted ' +
          'board by a hard gate</p>' +
          '<ul>' + record.blocking_reasons.map(function (reason) {
            return '<li><strong>' + esc(reason.gate) + ' ' + esc(gateName(reason.gate, gates)) +
              '</strong> — ' + esc(reason.detail) + ' ' +
              (reason.codes || []).map(function (c) { return codeChip(c, 'chip--danger'); }).join(' ') +
              '</li>';
          }).join('') + '</ul>' +
          '<p class="small">This run is shown on the Experimental board with the reason attached. ' +
          'It is not ranked against runs that passed.</p>' +
          '</div></section>';
      }

      /* decomposition — never the aggregate alone */
      html += '<section class="section"><h2>Score decomposition</h2>' +
        '<p class="section__lede">The aggregate is published alongside its eight dimensions; it is ' +
        'never shown on its own. These values are rendered exactly as the result generator wrote ' +
        'them — this page does not recompute them.</p>' +
        '<div class="table-scroll" tabindex="0" role="region" aria-label="Score decomposition">' +
        '<table class="plain"><caption class="visually-hidden">Eight dimension scores and the ' +
        'published aggregate for ' + esc(record.system) + ' on ' + esc(record.track) + '</caption>' +
        '<thead><tr><th scope="col">Dimension</th><th scope="col" class="col-num">Score</th>' +
        '<th scope="col">What it measures</th></tr></thead><tbody>' +
        dims.map(function (dim) {
          var raw = rawLabel(dim.key, record.raw);
          return '<tr><th scope="row">' + esc(dim.label) + '</th>' +
            '<td class="col-num">' + meterCell(scores[dim.key], { raw: raw }) + '</td>' +
            '<td class="small">' + esc(dim.definition || '') + '</td></tr>';
        }).join('') +
        '<tr><th scope="row">Aggregate</th><td class="col-num">' +
          meterCell(record.aggregate, { aggregate: true, ci: record.aggregate_ci95 }) + '</td>' +
          '<td class="small">Weighted geometric mean of the eight dimensions, eps = ' +
          esc((data.tracks.aggregation || {}).eps !== undefined ? data.tracks.aggregation.eps : 0.05) +
          '. Computed by the generator.</td></tr>' +
        '</tbody></table></div></section>';

      /* version axes */
      html += '<section class="section"><h2>Version axes</h2>' +
        '<p class="section__lede">Four axes version independently. A result is only reproducible ' +
        'if all four are named.</p>' +
        '<div class="card"><div class="card__body">' + versionAxes(record.versions) + '</div></div>' +
        '</section>';

      if (detail) {
        html += budgetSection(detail);
        html += traceSection(detail);
        html += evidenceSection(detail);
        html += artifactSection(detail);
        html += claimSection(detail, gates);
        html += provenanceSection(detail);
      } else {
        html += '<section class="section"><div class="callout callout--info">' +
          '<p class="callout__title">Trace not published for this run</p>' +
          '<p>The leaderboard row for this run is published, so its scores and its four version ' +
          'axes are shown above. The trace bundle (tool calls, evidence, artifacts, claim verdicts) ' +
          'is not in <code>runs.json</code>, so there is nothing further to render. The page ' +
          'degrades to what exists rather than inventing the rest.</p></div></section>';
      }

      html += '<p class="small"><a href="leaderboard.html?track=' + encodeURIComponent(record.track) +
        '&amp;board=' + encodeURIComponent(record.board) + '">← Back to the ' +
        esc(record.board) + ' board for ' + esc(record.track) + '</a></p>';

      host.innerHTML = html;
    }).catch(function (error) { renderError(host, error); });
  }

  function runPicker(runs, title, message) {
    return '<div class="callout callout--info"><p class="callout__title">' + esc(title) + '</p>' +
      '<p>' + message + '</p></div>' +
      '<div class="card"><div class="card__head"><h3 class="mb-0">Published run bundles</h3>' +
      '<span class="small muted">' + runs.length + ' in the example set</span></div>' +
      '<div class="card__body"><ul class="stack-sm">' + runs.map(function (run) {
        return '<li><a href="run.html?id=' + encodeURIComponent(run.run_id) + '">' +
          esc(run.system) + '</a> <span class="small muted">— ' + esc(run.track) +
          ' · ' + esc(run.board) + ' · ' + formatScore(run.aggregate) + '</span></li>';
      }).join('') + '</ul></div></div>' +
      '<p class="small"><a href="leaderboard.html">← Back to the leaderboard</a></p>';
  }

  function budgetSection(run) {
    var budget = run.budget;
    if (!budget) return '';
    var keys = ['tokens', 'tool_calls', 'wall_clock_s', 'cost_usd'];
    var labels = { tokens: 'Tokens', tool_calls: 'Tool calls', wall_clock_s: 'Wall clock (s)', cost_usd: 'Cost (USD)' };
    var rows = keys.filter(function (key) {
      return budget.consumed && isNum(budget.consumed[key]);
    }).map(function (key) {
      var used = Number(budget.consumed[key]);
      var limit = budget.limits && isNum(budget.limits[key]) ? Number(budget.limits[key]) : null;
      var share = limit ? Math.min(100, (used / limit) * 100) : 0;
      var over = limit !== null && used > limit;
      return '<div class="budget__row"><div class="budget__label">' +
        '<span>' + esc(labels[key]) + '</span>' +
        '<span class="mono">' + esc(formatCompact(used)) +
          (limit !== null ? ' / ' + esc(formatCompact(limit)) +
            ' (' + share.toFixed(0) + '%)' : ' (no limit declared)') + '</span></div>' +
        '<div class="budget__track"><div class="budget__fill' + (over ? ' budget__fill--over' : '') +
          '" style="width:' + (limit ? share : 100).toFixed(1) + '%"></div></div></div>';
    }).join('');
    if (!rows) return '';
    return '<section class="section"><h2>Budget consumed</h2>' +
      '<p class="section__lede">Metered against the declared limits. A run that exceeds a limit ' +
      'fails validation before it is scored, so no published row is over budget.</p>' +
      '<div class="card"><div class="card__body"><div class="budget">' + rows + '</div></div></div></section>';
  }

  function traceSection(run) {
    var trace = run.trace || [];
    if (!trace.length) return '';
    return '<section class="section"><h2>Trace</h2>' +
      '<p class="section__lede">What the run actually did, in order. The trace is part of the ' +
      'published bundle, so a reviewer can read the path as well as the outcome.</p>' +
      '<div class="card"><div class="card__body"><ol class="trace">' + trace.map(function (step) {
        var failed = step.status && step.status !== 'ok';
        return '<li class="trace__item' + (failed ? ' trace__item--fail' : '') + '">' +
          '<span class="trace__step" aria-hidden="true">' + esc(step.step) + '</span>' +
          '<div><span class="trace__name">' + esc(step.name) + '</span> ' +
          '<span class="trace__kind">' + esc(step.kind) + '</span>' +
          (failed ? ' <span class="badge badge--danger">' +
            '<span class="badge__glyph" aria-hidden="true">✕</span>' + esc(step.status) +
            '</span>' : '') +
          '<p class="trace__summary">' + esc(step.summary) + '</p>' +
          (step.note ? '<p class="trace__note">' + esc(step.note) + '</p>' : '') +
          '<p class="trace__meta">step ' + esc(step.step) +
            (isNum(step.duration_ms) ? ' · ' + esc(formatCompact(step.duration_ms)) + ' ms' : '') +
            (step.status ? ' · ' + esc(step.status) : '') + '</p>' +
          '</div></li>';
      }).join('') + '</ol></div></div></section>';
  }

  function evidenceSection(run) {
    var evidence = run.evidence || [];
    if (!evidence.length) return '';
    return '<section class="section"><h2>Evidence sources</h2>' +
      '<p class="section__lede">Each item names the source card it came from and the study design ' +
      'it actually is. Nothing here is placed on a tier above its design.</p>' +
      '<div class="table-scroll" tabindex="0" role="region" aria-label="Evidence sources">' +
      '<table class="plain"><thead><tr>' +
      '<th scope="col">Identifier</th><th scope="col">Design</th><th scope="col">Source card</th>' +
      '<th scope="col">Usable</th><th scope="col">Quote located</th>' +
      '</tr></thead><tbody>' + evidence.map(function (item) {
        var card = item.source_card || {};
        return '<tr><th scope="row" class="mono">' + esc(item.identifier_type) + ':' +
            esc(item.identifier) + '</th>' +
          '<td>' + esc(item.design) + (item.design === 'docking' || item.design === 'in_silico' ||
            item.design === 'network_prediction' ? ' <span class="badge badge--info" ' +
            'title="A computational prediction, not an observation of the world">prediction</span>' : '') + '</td>' +
          '<td class="small">' + esc(card.name || '—') +
            '<br><span class="mono small muted">' + esc((card.snapshot_hash || '').slice(0, 22)) +
            '… · ' + esc(formatDate(card.snapshot_at)) + '</span></td>' +
          '<td>' + (item.usable
            ? '<span class="badge badge--ok"><span class="badge__glyph" aria-hidden="true">✓</span>usable</span>'
            : '<span class="badge badge--danger"><span class="badge__glyph" aria-hidden="true">✕</span>unusable</span>') +
            (item.retracted ? ' <span class="badge badge--danger">retracted</span>' : '') + '</td>' +
          '<td class="small">' + esc(item.quote || '—') + '</td></tr>';
      }).join('') + '</tbody></table></div>' +
      '<p class="small muted">A quote is shown as the run recorded it. On the real bundle it is a ' +
      'located span in the source, not a paraphrase.</p></section>';
  }

  function artifactSection(run) {
    var artifacts = run.artifacts || [];
    if (!artifacts.length) return '';
    return '<section class="section"><h2>Artifacts produced</h2>' +
      '<p class="section__lede">Files the run declared, addressed by content. An output with no ' +
      'hash is a validation failure (ART107), not a formatting nit: it cannot be re-checked.</p>' +
      '<div class="card"><div class="card__body">' + artifacts.map(function (file) {
        return '<div class="artifact"><span class="artifact__path">' + esc(file.path) + '</span>' +
          '<span class="badge badge--plain">' + esc(file.media_type || 'unknown type') + '</span>' +
          '<span class="small muted">' + esc(formatCompact(file.bytes)) + ' bytes</span>' +
          (file.sha256
            ? '<span class="artifact__hash">sha256:' + esc(file.sha256) + '</span>'
            : '<span class="badge badge--danger"><span class="badge__glyph" aria-hidden="true">✕</span>' +
              'no content hash (ART107)</span>') +
          '<span class="artifact__desc">' + esc(file.description || '') + '</span></div>';
      }).join('') + '</div></div></section>';
  }

  function claimSection(run, gates) {
    var claims = run.claims || [];
    if (!claims.length) return '';
    var refused = claims.filter(function (c) { return !c.allowed; });
    var predictionRefusals = refused.filter(function (c) {
      return (c.codes || []).indexOf('CLM004') !== -1 || c.prediction_as_fact;
    });

    var html = '<section class="section"><h2>Claim-by-claim verdicts</h2>' +
      '<p class="section__lede">Every claim the run wanted to make, and whether it was allowed to. ' +
      'A refusal is an outcome, not a missing value: the claim is excluded from scoring and the ' +
      'code is published.</p>';

    if (predictionRefusals.length) {
      html += '<div class="callout callout--danger">' +
        '<p class="callout__title"><span aria-hidden="true">✕</span> Refused under the prediction ' +
        'prohibition (gate G4)</p>' +
        '<p>' + esc(PREDICTION_RULE) + '</p>' +
        '<p class="small">A clinical claim supported only by prediction is counted as severe ' +
        'overclaim and is reported separately from ordinary overclaiming, because it is the specific ' +
        'failure mode this benchmark exists to make visible.</p></div>';
    }

    html += claims.map(function (claim) {
      var codes = claim.codes || [];
      var hasCaveat = (claim.caveats || []).length > 0;
      var cls = !claim.allowed ? ' claim--refused' : (hasCaveat ? ' claim--caveat' : '');
      return '<div class="claim' + cls + '">' +
        '<div class="claim__head">' +
          '<span class="claim__id">' + esc(claim.claim_id) + '</span>' +
          '<span class="badge badge--plain">' + esc(claim.claim_kind) + '</span>' +
          (claim.allowed
            ? '<span class="badge badge--ok"><span class="badge__glyph" aria-hidden="true">✓</span>allowed</span>'
            : '<span class="badge badge--danger"><span class="badge__glyph" aria-hidden="true">✕</span>refused</span>') +
          (claim.prediction_as_fact
            ? '<span class="badge badge--danger">prediction stated as clinical fact</span>' : '') +
          (claim.needs_declaration
            ? '<span class="badge badge--warn">fixable by declaring the limit</span>' : '') +
          (isNum(claim.confidence) ? '<span class="badge badge--plain">stated confidence ' +
            formatScore(claim.confidence, { digits: 2 }) + '</span>' : '') +
          (claim.weakest_tier ? '<span class="badge badge--plain" title="Weakest evidence tier ' +
            'supporting this claim">weakest tier: ' + esc(claim.weakest_tier) + '</span>' : '') +
        '</div>' +
        '<p class="claim__text">“' + esc(claim.text) + '”</p>' +
        (codes.length ? '<div class="claim__codes">' + codes.map(function (code) {
            return codeChip(code, 'chip--danger');
          }).join('') + '</div>' : '') +
        ((claim.reasons || []).length ? '<ul class="claim__reasons">' +
          claim.reasons.map(function (reason) {
            return '<li><span class="mono">' + esc(reason.code) + '</span> — ' +
              esc(reason.detail) + '</li>';
          }).join('') + '</ul>' : '') +
        (hasCaveat ? '<ul class="claim__reasons">' + claim.caveats.map(function (caveat) {
            return '<li><span class="badge badge--warn">caveat</span> ' + esc(caveat) + '</li>';
          }).join('') + '</ul>' : '') +
        '</div>';
    }).join('');

    var allCodes = [];
    claims.forEach(function (claim) {
      (claim.codes || []).forEach(function (code) { if (allCodes.indexOf(code) === -1) allCodes.push(code); });
    });
    if (allCodes.length) {
      html += '<div class="card"><div class="card__head"><h3 class="mb-0">Refusal codes in this run</h3>' +
        '<span class="small muted">stable strings · never renumbered</span></div>' +
        '<div class="card__body"><dl class="kv"><dt class="mono">' +
        allCodes.map(function (code) { return esc(code); }).join('</dt><dd class="mono">') +
        '</dt><dd>' + allCodes.map(function (code) {
          return esc(CODE_MEANINGS[code] || 'not in the published table');
        }).join('</dd><dt class="mono">') + '</dd></dl></div></div>';
    }

    return html + '</section>';
  }

  function provenanceSection(run) {
    var html = '';
    if ((run.limitations || []).length) {
      html += '<section class="section"><h2>Limitations stated by the run</h2>' +
        '<p class="section__lede">An artifact that states no limitations does not pass validation ' +
        '(ART110). These are the run’s own words.</p>' +
        '<div class="card"><div class="card__body"><ul>' + run.limitations.map(function (item) {
          return '<li>' + esc(item) + '</li>';
        }).join('') + '</ul></div></div></section>';
    } else {
      html += '<section class="section"><div class="callout callout--warn">' +
        '<p class="callout__title">No limitations stated</p>' +
        '<p>This run declares no limitations. The artifact validator refuses an artifact with no ' +
        'stated limitations (ART110) — an artifact that claims nothing about its own scope has ' +
        'not earned the right to be read as a scientific statement.</p></div></section>';
    }
    if (run.notes) {
      html += '<section class="section"><div class="callout callout--info">' +
        '<p class="callout__title">Why this run is in the example set</p>' +
        '<p>' + esc(run.notes) + '</p></div></section>';
    }
    return html;
  }

  /* ----------------------------------------------------------------------
   * Compare
   * -------------------------------------------------------------------- */

  function renderCompare() {
    var host = $('#compare');
    if (!host) return;

    loadMany(['tracks', 'leaderboard']).then(function (data) {
      var dims = dimsFrom(data.tracks, data.leaderboard);
      var rows = data.leaderboard.runs || [];
      var tracks = (data.tracks.tracks || []).map(function (t) { return t.id; });

      var systems = [];
      rows.forEach(function (row) {
        if (!systems.some(function (s) { return s.slug === row.system_slug; })) {
          systems.push({ slug: row.system_slug, name: row.system, type: row.type });
        }
      });
      systems.sort(function (a, b) { return a.name.localeCompare(b.name); });

      var a = getParam('a') || (systems[0] && systems[0].slug);
      var b = getParam('b') || (systems[1] && systems[1].slug);

      function options(selected) {
        return systems.map(function (system) {
          return '<option value="' + esc(system.slug) + '"' +
            (system.slug === selected ? ' selected' : '') + '>' + esc(system.name) + '</option>';
        }).join('');
      }

      host.innerHTML =
        '<div class="card"><div class="card__body"><div class="filters">' +
        '<div class="field"><label class="field__label" for="compare-a">System A</label>' +
        '<select id="compare-a">' + options(a) + '</select></div>' +
        '<div class="field"><label class="field__label" for="compare-b">System B</label>' +
        '<select id="compare-b">' + options(b) + '</select></div>' +
        '</div></div></div>' +
        '<div id="compare-output" class="stack-lg"></div>';

      function draw() {
        var out = $('#compare-output');
        var rowsA = rows.filter(function (r) { return r.system_slug === a; });
        var rowsB = rows.filter(function (r) { return r.system_slug === b; });

        if (!rowsA.length || !rowsB.length) {
          out.innerHTML = '<div class="empty-state"><h3>Pick two published systems.</h3>' +
            '<p>Each side must have at least one published row in the season.</p></div>';
          return;
        }

        var nameA = rowsA[0].system;
        var nameB = rowsB[0].system;

        out.innerHTML = '<div class="card"><div class="card__body">' +
          '<p class="small muted mb-0">Comparing <strong>' + esc(nameA) + '</strong> with <strong>' +
          esc(nameB) + '</strong>, track by track. Scores are read from the published rows and the ' +
          'numbers are always shown, so the comparison never depends on seeing a highlight. A lead ' +
          'is marked with a glyph as well as a colour. A blocked system’s board is named on its ' +
          'panel.</p></div></div>' +
          tracks.map(function (trackId) {
            var ra = rowsA.filter(function (r) { return r.track === trackId; })[0];
            var rb = rowsB.filter(function (r) { return r.track === trackId; })[0];
            if (!ra && !rb) return '';
            return '<section class="compare-track">' +
              '<h3>' + esc(trackId) + '</h3>' +
              '<div class="compare-pair">' +
                comparePanel(ra, 'A') + comparePanel(rb, 'B') +
              '</div>' +
              comparisonTable(ra, rb, dims, nameA, nameB) +
              verdictLine(ra, rb, nameA, nameB) +
              '</section>';
          }).join('');
      }

      /* Header panel for one side: identity, board, and any blocking reason. */
      function comparePanel(row, side) {
        if (!row) {
          return '<div class="compare-panel"><p class="compare-panel__name">Not published' +
            ' (side ' + esc(side) + ')</p>' +
            '<p class="compare-panel__meta">No row on this track.</p></div>';
        }
        var blocked = (row.blocking_reasons || []).length > 0;
        return '<div class="compare-panel' + (blocked ? ' compare-panel--blocked' : '') + '">' +
          '<p class="compare-panel__name">' + esc(row.system) + '</p>' +
          '<p class="compare-panel__meta">' + esc(row.type) + ' · v' + esc(row.version) +
            ' · ' + (isNum(row.rank) ? 'rank #' + esc(row.rank) + ' · ' : '') +
            esc(row.board) + '</p>' +
          (blocked ? '<p class="small" style="margin:0"><span aria-hidden="true">⚠</span> ' +
            'Blocked · ' + (row.blocking_reasons || []).map(function (reason) {
              return esc(reason.gate) + ' ' + esc(reason.detail);
            }).join(' ') + '</p>' : '') +
          '</div>';
      }

      /* The per-dimension comparison. Decomposition is the point: two systems
       * can share an aggregate and disagree on every dimension under it. */
      function comparisonTable(ra, rb, dims, nameA, nameB) {
        if (!ra || !rb) return '';
        function cell(value, mine, theirs) {
          var hasBoth = isNum(mine) && isNum(theirs);
          var leads = hasBoth && Number(mine) > Number(theirs);
          return '<td class="dim-row__value' + (leads ? ' dim-row__value--win' : '') +
            (isNum(value) ? '' : ' dim-row__value--null') + '">' +
            formatScore(value) +
            (leads ? ' <span aria-hidden="true">▲</span><span class="visually-hidden">higher</span>' : '') +
            '</td>';
        }
        var rows = dims.map(function (dim) {
          var va = ra.scores ? ra.scores[dim.key] : null;
          var vb = rb.scores ? rb.scores[dim.key] : null;
          return '<tr><th scope="row" class="dim-row__label">' + esc(dim.label) + '</th>' +
            cell(va, va, vb) + cell(vb, vb, va) + '</tr>';
        }).join('');
        return '<div class="table-scroll" tabindex="0" role="region" aria-label="Comparison for ' +
          esc(ra.track) + '"><table class="plain"><caption class="visually-hidden">Dimension scores ' +
          'for ' + esc(nameA) + ' and ' + esc(nameB) + ' on ' + esc(ra.track) + '</caption>' +
          '<thead><tr><th scope="col">Dimension</th>' +
          '<th scope="col" class="col-num">' + esc(nameA) + '</th>' +
          '<th scope="col" class="col-num">' + esc(nameB) + '</th></tr></thead>' +
          '<tbody>' + rows +
          '<tr><th scope="row" class="dim-row__label">Aggregate</th>' +
            cell(ra.aggregate, ra.aggregate, rb.aggregate) +
            cell(rb.aggregate, rb.aggregate, ra.aggregate) + '</tr>' +
          '</tbody></table></div>';
      }

      function verdictLine(ra, rb, nameA, nameB) {
        if (!ra || !rb) return '';
        var winsA = 0, winsB = 0, ties = 0;
        dims.forEach(function (dim) {
          var va = ra.scores ? Number(ra.scores[dim.key]) : NaN;
          var vb = rb.scores ? Number(rb.scores[dim.key]) : NaN;
          if (!isFinite(va) || !isFinite(vb)) return;
          if (va > vb) winsA++; else if (vb > va) winsB++; else ties++;
        });
        var parts = [];
        parts.push('<li>Dimensions led (<span aria-hidden="true">▲</span>): <strong>' + esc(nameA) +
          '</strong> ' + winsA + ' · <strong>' + esc(nameB) + '</strong> ' + winsB +
          (ties ? ' · tied ' + ties : '') + ' — of ' + dims.length + '.</li>');
        parts.push('<li>Published aggregate: ' + formatScore(ra.aggregate) + ' vs ' +
          formatScore(rb.aggregate) + '.</li>');
        if (ra.board !== rb.board) {
          parts.push('<li>Board: ' + esc(nameA) + ' is <strong>' + esc(ra.board) + '</strong>, ' +
            esc(nameB) + ' is <strong>' + esc(rb.board) + '</strong>. A board difference is a gate ' +
            'difference, not a score difference: these two rows are not ranked against each other, ' +
            'and the aggregate comparison above is reported for completeness only.</li>');
        }
        return '<ul class="small muted" style="margin-top:0.75rem">' + parts.join('') + '</ul>';
      }

      function refresh() { setParams({ a: a, b: b }); draw(); }

      $('#compare-a').addEventListener('change', function (e) { a = e.target.value; refresh(); });
      $('#compare-b').addEventListener('change', function (e) { b = e.target.value; refresh(); });
      draw();
      renderDataBanner();
    }).catch(function (error) { renderError(host, error); });
  }

  /* ----------------------------------------------------------------------
   * Benchmarks
   * -------------------------------------------------------------------- */

  function renderBenchmarks() {
    var host = $('#benchmarks');
    if (!host) return;

    loadMany(['benchmarks', 'tracks']).then(function (data) {
      var doc = data.benchmarks;
      var tracksDoc = data.tracks;
      var gates = gatesFrom(tracksDoc);
      var seasons = doc.seasons || [];
      var current = seasons.filter(function (s) { return s.season === doc.current_season; })[0] || seasons[0];

      $('#benchmark-seasons').innerHTML = seasons.map(function (season) {
        var isCurrent = current && season.season === current.season;
        return '<div class="card"><div class="card__head">' +
          '<h3 class="mb-0">Season ' + esc(season.season) + '</h3>' +
          '<span class="badge ' + (isCurrent ? 'badge--info' : 'badge--plain') + '">' +
            (isCurrent ? 'current' : 'reference') + '</span>' +
          '<span class="badge badge--ok" title="A Season, once cut, is immutable">' +
            '<span class="badge__glyph" aria-hidden="true">✓</span>' + esc(season.status) + '</span>' +
          '</div><div class="card__body">' +
          '<dl class="kv">' +
            '<dt>Version</dt><dd class="mono">' + esc(season.version) + '</dd>' +
            '<dt>Date frozen</dt><dd>' + esc(formatDate(season.frozen_on)) + '</dd>' +
            '<dt>Cases</dt><dd>' + esc(season.cases_total) + '</dd>' +
            '<dt>Splits</dt><dd>' + (season.splits || []).map(function (split) {
              return '<span class="chip">' + esc(split.name) + ' ' + esc(split.cases) + '</span>';
            }).join(' ') + '</dd>' +
          '</dl>' +
          '<p class="small muted" style="margin-top:0.85rem">' + esc(season.note) + '</p>' +
          '</div></div>';
      }).join('');

      if (current) {
        $('#benchmark-tracks').innerHTML = (current.tracks || []).map(function (track) {
          var full = (tracksDoc.tracks || []).filter(function (t) { return t.id === track.id; })[0] || {};
          return '<tr><th scope="row">' + esc(track.id) + '<div class="small muted">' +
            esc(full.question || '') + '</div></th>' +
            '<td>' + (track.metric_focus || []).map(function (m) {
              return '<span class="chip">' + esc(m) + '</span>';
            }).join(' ') + '</td>' +
            '<td class="col-num">' + esc(track.cases) + '</td>' +
            '<td class="small">' + esc(full.notes || '') + '</td></tr>';
        }).join('');
      }

      var sources = (current && current.sources) || [];
      if (!sources.length) {
        $('#benchmark-sources').innerHTML = '<tr><td colspan="5" class="small muted">' +
          'Data sources are listed for the current Season only. This Season is kept for reference ' +
          'and its source cards are not republished here.</td></tr>';
      } else {
        $('#benchmark-sources').innerHTML = sources.map(function (source) {
          return '<tr><th scope="row">' + esc(source.name) +
            '<div class="small muted">' + esc(source.role) + '</div></th>' +
            '<td>' + esc(source.licence) + ' ' + (source.licence_verified
              ? '<span class="badge badge--ok"><span class="badge__glyph" aria-hidden="true">✓</span>verified</span>'
              : '<span class="badge badge--warn"><span class="badge__glyph" aria-hidden="true">⚠</span>unverified</span>') +
            '</td>' +
            '<td class="mono small">' + esc((source.snapshot_hash || '').slice(0, 20)) + '…' +
              '<div class="small muted">' + esc(formatDate(source.snapshot_at)) + '</div></td>' +
            '<td>' + (source.used_by || []).map(function (t) {
              return '<span class="chip">' + esc(t) + '</span>';
            }).join(' ') + '</td>' +
            '<td class="small">' + (source.url ? '<a href="' + esc(source.url) + '" rel="noopener">' +
              esc(source.url.replace(/^https?:\/\//, '').replace(/\/$/, '')) + '</a>' : '—') + '</td></tr>';
        }).join('');
      }

      $('#benchmark-rules').innerHTML = (doc.scoring_rules || []).map(function (rule) {
        return '<details class="disclosure"><summary><span class="mono">' + esc(rule.id) + '</span> — ' +
          esc(rule.rule) + '</summary>' +
          '<p class="small">' + esc(rule.detail) + '</p>' +
          '<p class="small muted">Applies to: ' + esc(rule.applies_to) + '</p></details>';
      }).join('');

      $('#benchmark-gates').innerHTML = gates.map(function (gate) {
        return '<div class="gate"><div class="gate__head">' +
          '<span class="gate__id">' + esc(gate.id) + '</span>' +
          '<span class="gate__name">' + esc(gate.name) + '</span>' +
          '<span class="gate__rule">' + esc(gate.rule) + '</span>' +
          (gate.codes || []).map(function (code) { return codeChip(code); }).join(' ') +
          '</div><p class="gate__text">' + esc(gate.rationale) + '</p></div>';
      }).join('');

      var citation = doc.citation || {};
      $('#benchmark-citation').innerHTML =
        '<p>' + esc(citation.text || '') + '</p>' +
        (citation.bibtex ? '<pre><code>' + esc(citation.bibtex) + '</code></pre>' : '');

      renderDataBanner();
    }).catch(function (error) { renderError(host, error); });
  }

  /* ----------------------------------------------------------------------
   * Skills
   * -------------------------------------------------------------------- */

  function statusBadge(skill) {
        var approval = skill.approval || {};
        if (skill.status === 'stable') {
          return '<span class="badge badge--ok"><span class="badge__glyph" aria-hidden="true">✓</span>stable</span>';
        }
        if (approval.status === 'held') {
          return '<span class="badge badge--warn"><span class="badge__glyph" aria-hidden="true">⚠</span>candidate · held</span>';
        }
        return '<span class="badge badge--plain">candidate</span>';
      }

  /* ---------------------------------------------------------------------
   * Skill registry: a summary row plus a detail row.
   *
   * The table alone said *which* skills exist. A reader wants to know what
   * each one does, what it may cite, what it refuses, and where its code is —
   * all of which is in the manifest and none of which fits in a table cell.
   * The detail row is collapsed by default so the list stays scannable.
   * ------------------------------------------------------------------- */
  function lang() {
    return document.documentElement.getAttribute('data-lang') === 'zh' ? 'zh' : 'en';
  }

  function pick(en, zh) {
    return lang() === 'zh' ? (zh || en || '') : (en || '');
  }

  function chipList(items) {
    if (!items || !items.length) return '<span class="muted small">—</span>';
    return '<ul class="chip-list">' + items.map(function (x) {
      return '<li class="chip">' + esc(x) + '</li>';
    }).join('') + '</ul>';
  }

  function skillRow(skill, status) {
    return '<tr data-status="' + esc(status) + '" class="skill-row">' +
      '<th scope="row">' +
        '<button type="button" class="skill-toggle" data-skill="' + esc(skill.id) + '"' +
          ' aria-expanded="false" aria-controls="detail-' + esc(skill.id) + '">' +
          '<span class="skill-toggle__glyph" aria-hidden="true">▸</span>' +
          '<span>' + esc(pick(skill.name, skill.name_zh)) +
            '<span class="mono small muted" style="display:block">' + esc(skill.id) +
            '</span>' +
          '</span>' +
        '</button>' +
        '<div class="small muted" style="margin-top:0.25rem">' +
          esc(pick(skill.summary, skill.summary_zh)) + '</div>' +
      '</th>' +
      '<td>' + statusBadge(skill) + '</td>' +
      '<td class="mono">' + esc(skill.version) +
        '<div class="small muted">api ' + esc(skill.api_version) + '</div></td>' +
      '<td class="small">' + esc(skill.source_repo || '—') +
        '<div class="mono small muted">@' + esc(skill.commit || '') + '</div></td>' +
      '<td class="small">' + esc(skill.licence) + ' ' + (skill.licence_verified
        ? '<span class="badge badge--ok"><span class="badge__glyph" aria-hidden="true">✓</span>verified</span>'
        : '<span class="badge badge--warn"><span class="badge__glyph" aria-hidden="true">⚠</span>unverified</span>') +
      '</td>' +
      '<td>' + chipList(skill.permissions) + '</td>' +
      '<td class="small">' + (deltaCell(skill)) + '</td>' +
      '</tr>';
  }

  function deltaCell(skill) {
    if (skill.benchmark_delta && skill.benchmark_delta.aggregate !== undefined) {
      return esc(String(skill.benchmark_delta.aggregate));
    }
    return '<span class="muted" title="' + esc(skill.benchmark_delta_note || '') + '">—</span>';
  }

  function skillDetail(skill, status) {
    var ev = skill.evidence || {};
    var rt = skill.runtime || {};
    var ap = skill.approval || {};
    function kv(label, labelZh, value) {
      return '<dt>' + esc(pick(label, labelZh)) + '</dt><dd>' + value + '</dd>';
    }
    return '<tr class="skill-detail" id="detail-' + esc(skill.id) + '" hidden' +
      ' data-status="' + esc(status) + '"><td colspan="7"><div class="skill-detail__inner">' +

      '<div class="skill-detail__col">' +
        '<h4>' + esc(pick('What it refuses to do', '它拒绝做什么')) + '</h4>' +
        '<p>' + esc(pick(skill.refuses, skill.refuses_zh)) + '</p>' +
        '<h4>' + esc(pick('Evidence policy', '证据政策')) + '</h4>' +
        '<dl class="kv">' +
          kv('Highest evidence tier', '可引用的最高证据层级',
             '<span class="mono">' + esc(ev.max_tier) + '</span> — ' +
             esc(ev.max_tier_zh || '')) +
          kv('Claim kinds it may emit', '可主张的类型',
             chipList(ev.claim_kinds_zh || ev.claim_kinds)) +
          kv('Claim kinds forbidden', '禁止主张的类型',
             chipList(ev.forbidden_claims_zh || ev.forbidden_claims)) +
          kv('Sources must be pinned', '来源必须钉住',
             ev.require_pinned_sources ? (lang() === 'zh' ? '是' : 'Yes')
                                       : (lang() === 'zh' ? '否' : 'No')) +
          kv('Quotes must be verified', '引文必须核验',
             ev.require_quote_verified ? (lang() === 'zh' ? '是' : 'Yes')
                                       : (lang() === 'zh' ? '否' : 'No')) +
        '</dl>' +
      '</div>' +

      '<div class="skill-detail__col">' +
        '<h4>' + esc(pick('How to run it', '怎么调用')) + '</h4>' +
        '<pre class="code">python -m bioagent.cli skill ' + esc(skill.id) +
        '\n    --arg &lt;name&gt;=&lt;value&gt;' +
        '\n    --dir BioScience-Harness/skills/tcm</pre>' +
        '<h4>' + esc(pick('Runtime', '运行方式')) + '</h4>' +
        '<dl class="kv">' +
          kv('Entry point', '入口', '<code class="mono small">' +
             esc(skill.entrypoint || '—') + '</code>') +
          kv('Backend', '后端', '<span class="mono">' + esc(rt.backend || '—') +
             '</span>') +
          kv('Timeout', '超时', esc((rt.timeout_s || 0) + ' s')) +
          kv('Deterministic', '确定性',
             rt.deterministic ? (lang() === 'zh' ? '是' : 'Yes')
                              : (lang() === 'zh' ? '否' : 'No')) +
          kv('Content hash', '内容哈希',
             '<code class="mono small">' + esc((skill.content_hash || '').slice(0, 16)) +
             '…</code>') +
          kv('Reads sources', '读取的数据源', chipList(skill.sources)) +
        '</dl>' +
      '</div>' +

      '<div class="skill-detail__col">' +
        '<h4>' + esc(pick('Approval', '批准记录')) + '</h4>' +
        '<dl class="kv">' +
          kv('Approved by', '批准人', esc(ap.approved_by || '—')) +
          kv('Approved at', '批准时间', esc(ap.approved_at || '—')) +
        '</dl>' +
        '<h4>' + esc(pick('Documentation', '文档')) + '</h4>' +
        '<details class="skill-doc"><summary>' +
          esc(pick('Read SKILL.md', '阅读 SKILL.md')) + '</summary>' +
          '<pre class="skill-doc__body">' + esc(skill.documentation || '') + '</pre>' +
        '</details>' +
      '</div>' +

      '</div></td></tr>';
  }

  function bindSkillDetail() {
    /* `?skill=<id>` opens one detail panel, so a link can point at a specific
     * skill and a screenshot can capture it without a click. */
    var wanted = null;
    try { wanted = new URLSearchParams(window.location.search).get('skill'); }
    catch (e) { /* no URLSearchParams */ }

    Array.prototype.forEach.call(
      document.querySelectorAll('.skill-toggle'),
      function (button) {
        button.addEventListener('click', function () {
          var id = button.getAttribute('data-skill');
          var row = document.getElementById('detail-' + id);
          if (!row) return;
          var open = row.hasAttribute('hidden');
          if (open) { row.removeAttribute('hidden'); } else { row.setAttribute('hidden', ''); }
          button.setAttribute('aria-expanded', String(open));
          button.querySelector('.skill-toggle__glyph').textContent = open ? '▾' : '▸';
        });
        /* Open the deep-linked one *after* its listener is attached; calling
         * click() before this point dispatched into nothing, which is why
         * ?skill= rendered a collapsed row. */
        if (wanted && button.getAttribute('data-skill') === wanted) {
          button.click();
        }
      });
  }

  function renderSkills() {
    var host = $('#skills');
    if (!host) return;

    loadMany(['skills']).then(function (data) {
      var doc = data.skills;
      var registries = doc.registries || {};
      var skills = doc.skills || [];

      $('#skill-registries').innerHTML = ['candidate', 'stable', 'benchmark'].map(function (key) {
        var registry = registries[key] || {};
        return '<div class="card"><div class="card__head"><h3 class="mb-0">' +
          esc(titleCase(key)) + ' registry</h3></div><div class="card__body">' +
          '<dl class="kv">' +
            '<dt>Revision</dt><dd class="mono">' + esc(registry.revision || '—') + '</dd>' +
            '<dt>Updated</dt><dd>' + esc(formatDate(registry.updated)) + '</dd>' +
            (registry.count !== undefined ? '<dt>Entries</dt><dd>' + esc(registry.count) + '</dd>' : '') +
          '</dl>' +
          '<p class="small muted" style="margin-top:0.75rem">' + esc(registry.role || '') + '</p>' +
          '</div></div>';
      }).join('');

      var statuses = [];
      skills.forEach(function (skill) {
        var key = skill.status + (skill.approval && skill.approval.status === 'held' ? ':held' : '');
        if (statuses.indexOf(key) === -1) statuses.push(key);
      });
      $('#skill-filter').innerHTML = '<option value="all">All statuses</option>' +
        statuses.map(function (status) {
          var parts = status.split(':');
          var label = titleCase(parts[0]) + (parts[1] ? ' · ' + parts[1] : '');
          return '<option value="' + esc(status) + '">' + esc(label) + '</option>';
        }).join('');


      function draw() {
        var filter = $('#skill-filter').value;
        var shown = skills.filter(function (skill) {
          if (filter === 'all') return true;
          var key = skill.status + (skill.approval && skill.approval.status === 'held' ? ':held' : '');
          return key === filter;
        });
        $('#skill-count').textContent = shown.length + ' of ' + skills.length + ' entries';

        $('#skill-body').innerHTML = shown.map(function (skill) {
          var approval = skill.approval || {};
          var delta = skill.benchmark_delta || {};
          var status = skill.status + (approval.status === 'held' ? ':held' : '');
          return skillRow(skill, status) + skillDetail(skill, status);
        }).join('');
        bindSkillDetail();
        return;

        var _unused = shown.map(function (skill) {
          var approval = skill.approval || {};
          var status = skill.status + (approval.status === 'held' ? ':held' : '');
          return '<tr data-status="' + esc(status) + '">' +
            '<th scope="row">' + esc(skill.name) +
              '<div class="mono small muted">' + esc(skill.id) + '</div>' +
              '<div class="small muted">' + esc(skill.notes || '') + '</div></th>' +
            '<td>' + statusBadge(skill) + '</td>' +
            '<td class="mono">' + esc(skill.version) +
              '<div class="small muted">api ' + esc(skill.api_version) + '</div></td>' +
            '<td class="small">' + esc(skill.source_repo || '—') +
              '<div class="mono small muted">@' + esc(skill.commit || '') + '</div></td>' +
            '<td class="small">' + esc(skill.licence) + ' ' + (skill.licence_verified
              ? '<span class="badge badge--ok"><span class="badge__glyph" aria-hidden="true">✓</span>verified</span>'
              : '<span class="badge badge--warn" title="Licence string not yet verified by a reviewer">' +
                '<span class="badge__glyph" aria-hidden="true">⚠</span>unverified</span>') + '</td>' +
            '<td><ul class="chip-list">' + (skill.permissions || []).map(function (permission) {
              var risky = /network:(any|herb|pubmed|bindingdb|openfda)|exec:|fs:write/.test(permission);
              return '<li><span class="chip' + (risky ? ' chip--warn' : '') + '">' +
                (risky ? '<span aria-hidden="true">⚠</span> ' : '') + esc(permission) + '</span></li>';
            }).join('') + '</ul></td>' +
            '<td class="small">' + (delta.track
              ? esc(delta.track) + '<div class="mono">' + esc(delta.metric) + ' ' +
                formatDelta(delta.delta) + '</div><div class="small muted">n = ' + esc(delta.n) +
                (delta.delta === null || delta.delta === undefined ? ' · not benchmarked' : '') + '</div>'
              : 'not measured') +
              (delta.note ? '<div class="small muted">' + esc(delta.note) + '</div>' : '') + '</td>' +
            '<td class="small">' + (approval.status
              ? '<span class="badge ' + (approval.status === 'approved' ? 'badge--ok' :
                  approval.status === 'held' ? 'badge--warn' : 'badge--plain') + '">' +
                esc(approval.status) + '</span>' +
                (approval.decision_id ? '<div class="mono small muted">' + esc(approval.decision_id) + '</div>' : '') +
                (approval.decided_by ? '<div class="small muted">' + esc(approval.decided_by) + ' · ' +
                  esc(formatDate(approval.decided_on)) + '</div>' : '')
              : '<span class="badge badge--plain">awaiting review</span>') + '</td>' +
            '</tr>';
        }).join('');
      }

      $('#skill-filter').addEventListener('change', draw);
      $('#skill-rule').textContent = doc.promotion_rule || '';
      draw();
      renderDataBanner();
    }).catch(function (error) { renderError(host, error); });
  }

  /* ----------------------------------------------------------------------
   * Methods
   *
   * The argument on this page is prose, but every number in it — the eps
   * floor, the weights, the gate rules, the dimension definitions — is read
   * from the Season file rather than restated in HTML. A methods page that
   * disagrees with the data it describes is worse than no methods page.
   * -------------------------------------------------------------------- */

  function renderMethods() {
    var host = $('#methods-data');
    if (!host) return;

    loadMany(['tracks', 'benchmarks']).then(function (data) {
      var tracksDoc = data.tracks;
      var dims = tracksDoc.dimensions || [];
      var gates = gatesFrom(tracksDoc);
      var aggregation = tracksDoc.aggregation || {};
      var season = tracksDoc.season_label || tracksDoc.season || '—';

      $('#methods-aggregation').innerHTML =
        '<div class="formula">' + esc(aggregation.formula ||
          'aggregate = exp( sum_i w_i * ln(max(x_i, eps)) / sum_i w_i )') + '</div>' +
        '<dl class="kv">' +
          '<dt>Method</dt><dd>' + esc(aggregation.method || 'weighted geometric mean') + '</dd>' +
          '<dt>Floor (eps)</dt><dd class="mono">' + esc(aggregation.eps) + '</dd>' +
          '<dt>Season</dt><dd>' + esc(season) + '</dd>' +
          '<dt>Computed by</dt><dd>the result generator, not this page</dd>' +
        '</dl>' +
        '<p class="small muted" style="margin-top:0.85rem">' + esc(aggregation.note || '') + '</p>';

      $('#methods-dimensions').innerHTML = dims.map(function (dim) {
        return '<tr><th scope="row">' + esc(dim.label) +
          '<div class="mono small muted">' + esc(dim.key) + '</div></th>' +
          '<td class="col-num mono">' + esc(dim.weight) + '</td>' +
          '<td>' + esc(dim.direction === 'higher' ? 'higher is better' : esc(dim.direction)) + '</td>' +
          '<td class="small">' + esc(dim.definition) + '</td></tr>';
      }).join('');

      $('#methods-weights-note').textContent =
        'Weights are published per Season and currently sum to ' +
        dims.reduce(function (sum, dim) { return sum + (Number(dim.weight) || 0); }, 0).toFixed(3) +
        '. A weighted geometric mean is used rather than an arithmetic one because the ' +
        'dimensions are not substitutes: a system that cannot ground its evidence is not ' +
        'half-credited for being fast.';

      $('#methods-gates').innerHTML = gates.map(function (gate) {
        return '<div class="gate"><div class="gate__head">' +
          '<span class="gate__id">' + esc(gate.id) + '</span>' +
          '<span class="gate__name">' + esc(gate.name) + '</span>' +
          '<span class="gate__rule">' + esc(gate.rule) + '</span>' +
          '</div>' +
          '<p class="small muted" style="margin:0.35rem 0 0">Threshold: ' + esc(gate.threshold) +
          ' · ' + (gate.codes || []).map(function (code) { return codeChip(code); }).join(' ') + '</p>' +
          '<p class="gate__text">' + esc(gate.rationale) + '</p></div>';
      }).join('');

      var current = (data.benchmarks.seasons || []).filter(function (s) {
        return s.season === data.benchmarks.current_season;
      })[0] || {};
      $('#methods-season').innerHTML = '<dl class="kv">' +
        '<dt>Season</dt><dd class="mono">' + esc(current.season || '—') + '</dd>' +
        '<dt>Version</dt><dd class="mono">' + esc(current.version || '—') + '</dd>' +
        '<dt>Frozen on</dt><dd>' + esc(formatDate(current.frozen_on)) + '</dd>' +
        '<dt>Cases</dt><dd>' + esc(current.cases_total || '—') + '</dd>' +
        '</dl>';

      renderDataBanner();
    }).catch(function (error) { renderError(host, error); });
  }

  /* ----------------------------------------------------------------------
   * Dispatch
   * -------------------------------------------------------------------- */

  function init() {
    var page = document.body.getAttribute('data-page');
    if (page === 'overview') renderOverview();
    else if (page === 'leaderboard') {
      renderLeaderboard();
      /* Re-render on a language change: the header row and the captions are
       * built by script, so they do not follow the attribute switch by
       * themselves. */
      window.addEventListener('tcmscience:langchange', function () {
        /* Re-run the render. `renderLeaderboard` writes into the containers it
         * finds, so clearing them here destroyed the table skeleton it expects
         * and threw "Cannot set properties of null". The render is idempotent;
         * it only needs to be called again. */
        renderLeaderboard();
      });
    }
    else if (page === 'run') renderRun();
    else if (page === 'compare') renderCompare();
    else if (page === 'benchmarks') renderBenchmarks();
    else if (page === 'skills') renderSkills();
    else if (page === 'methods') renderMethods();

    /* submit.html is prose and a process description: it has no data to render
     * and nothing that can fail to load. */
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

/* ---------------------------------------------------------------------------
 * Language
 *
 * The attribute lives on <html> and CSS does the switching, so the correct
 * language shows before this script runs. This only wires the buttons and
 * remembers the choice.
 * --------------------------------------------------------------------------- */
var LANG_KEY = 'tcmscience.lang';

function currentLang() {
  /* A `?lang=zh` query wins, so a link can point at a specific language and a
   * screenshot or a crawler can request one deterministically. Then the stored
   * choice, then English. */
  try {
    var q = new URLSearchParams(window.location.search).get('lang');
    if (q === 'zh' || q === 'en') { return q; }
  } catch (e) { /* no URLSearchParams: fall through */ }
  try { return localStorage.getItem(LANG_KEY) || 'en'; } catch (e) { return 'en'; }
}

function applyLang(lang) {
  document.documentElement.setAttribute('data-lang', lang);
  document.documentElement.setAttribute('lang', lang === 'zh' ? 'zh-Hans' : 'en');
  try { localStorage.setItem(LANG_KEY, lang); } catch (e) { /* private mode */ }
  Array.prototype.forEach.call(
    document.querySelectorAll('.lang-switch button'),
    function (b) { b.setAttribute('aria-pressed', String(b.dataset.lang === lang)); });
  /* Tables are built by script, so the attribute change alone does not reach
   * them — their headers would stay in the previous language. Re-dispatch the
   * render rather than reloading the page, which would lose the filter state. */
  try {
    window.dispatchEvent(new CustomEvent('tcmscience:langchange', { detail: lang }));
  } catch (e) { /* very old browser: headers stay in the load-time language */ }
}

function initLang() {
  applyLang(currentLang());
  Array.prototype.forEach.call(
    document.querySelectorAll('.lang-switch button'),
    function (b) {
      b.addEventListener('click', function () { applyLang(b.dataset.lang); });
    });
}

/* Run before anything else so the page never shows the wrong language. */
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initLang);
} else {
  initLang();
}
