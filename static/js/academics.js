/* ============================================================
   Academics module interactions
   ------------------------------------------------------------
   Everything here is progressive enhancement: the pages already
   render usable server-side markup, and this file only adds
   charts, the calendar widget, mark-entry helpers, table filters
   and unsaved-change guards. It never changes any business rule
   (marks, grades, approval states) — those stay on the server.
   ============================================================ */

(function () {
    'use strict';

    var AC = window.AC || {};
    window.AC = AC;

    function qs(sel, root) { return (root || document).querySelector(sel); }
    function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

    function toast(type, title, message) {
        if (typeof window.showToast === 'function') {
            window.showToast(type, title, message);
        }
    }

    /* ------------------------------------------
       CHART.JS HELPERS
       Charts read their data from a json_script
       block so no data is hard-coded in JS.
       ------------------------------------------ */
    var CHART_DEFAULTS = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: true, labels: { boxWidth: 12, usePointStyle: true } },
            tooltip: { backgroundColor: '#0f172a', padding: 10, cornerRadius: 6 }
        }
    };

    function readJson(id) {
        var el = document.getElementById(id);
        if (!el) return null;
        try { return JSON.parse(el.textContent); } catch (e) { return null; }
    }

    function initCharts() {
        if (typeof window.Chart === 'undefined') return;
        qsa('canvas[data-chart]').forEach(function (canvas) {
            var data = readJson(canvas.getAttribute('data-chart'));
            if (!data) return;
            var type = data.type || canvas.getAttribute('data-chart-type') || 'bar';
            var datasets = (data.datasets || []).map(function (ds) {
                var out = Object.assign({}, ds);
                if (type === 'line' && out.fill === undefined) out.fill = false;
                if (out.tension === undefined && type === 'line') out.tension = 0.35;
                if (out.borderRadius === undefined && (type === 'bar')) out.borderRadius = 6;
                return out;
            });
            var options = Object.assign({}, CHART_DEFAULTS, data.options || {});
            options.plugins = Object.assign({}, CHART_DEFAULTS.plugins, (data.options || {}).plugins || {});
            if (data.y_begin_at_zero !== false && (type === 'bar' || type === 'line')) {
                options.scales = Object.assign({
                    y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: '#eef2f7' } },
                    x: { grid: { display: false } }
                }, options.scales || {});
            }
            /* eslint-disable-next-line no-new */
            new window.Chart(canvas.getContext('2d'), {
                type: type,
                data: { labels: data.labels || [], datasets: datasets },
                options: options
            });
        });
    }

    /* ------------------------------------------
       CALENDAR (month / week / agenda)
       Renders entirely from the server-provided
       event list. Navigation stays client-side.
       ------------------------------------------ */
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function toKey(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
    function parseKey(s) {
        var p = String(s).split('-');
        return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
    }
    var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
        'August', 'September', 'October', 'November', 'December'];
    var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(str == null ? '' : String(str)));
        return div.innerHTML;
    }

    function initCalendar() {
        var root = qs('[data-calendar-root]');
        var mount = qs('#acCalendar');
        if (!root || !mount) return;
        var payload = readJson('acCalendarData');
        if (!payload) return;

        var events = payload.events || [];
        var canManage = !!payload.canManage;
        var today = payload.today ? parseKey(payload.today) : new Date();
        var view = 'month';
        var cursor = new Date(today.getFullYear(), today.getMonth(), 1);
        var dayFocus = null;
        var active = {};

        var titleEl = qs('#acCalTitle');
        var detailEl = qs('#acCalDetail');

        function visibleEvents() {
            var keys = Object.keys(active).filter(function (k) { return active[k]; });
            if (!keys.length) return events;
            return events.filter(function (e) { return keys.indexOf(e.type) !== -1; });
        }

        function eventsOnDay(key) {
            return visibleEvents().filter(function (e) {
                var start = e.start, end = e.end || e.start;
                return key >= start && key <= end;
            });
        }

        function chip(e) {
            var label = escapeHtml(e.title);
            var inner = '<span class="ac-event-dot type-dot-' + e.type + '"></span>' + label;
            if (canManage && e.editUrl) {
                return '<a class="ac-cal-chip type-' + e.type + '" href="' + e.editUrl +
                    '" title="' + escapeHtml(e.title) + '">' + inner + '</a>';
            }
            return '<span class="ac-cal-chip type-' + e.type + '" title="' +
                escapeHtml(e.title) + '">' + inner + '</span>';
        }

        function renderMonth() {
            var first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
            var startOffset = first.getDay();
            var gridStart = new Date(first);
            gridStart.setDate(first.getDate() - startOffset);
            var html = '<div class="ac-cal-grid">';
            DOW.forEach(function (d) { html += '<div class="ac-cal-dow">' + d + '</div>'; });
            for (var i = 0; i < 42; i++) {
                var day = new Date(gridStart);
                day.setDate(gridStart.getDate() + i);
                var key = toKey(day);
                var inMonth = day.getMonth() === cursor.getMonth();
                var cls = 'ac-cal-day' + (inMonth ? '' : ' is-other') +
                    (key === toKey(today) ? ' is-today' : '');
                var dayEvents = eventsOnDay(key);
                var shown = dayEvents.slice(0, 3);
                html += '<div class="' + cls + '" data-cal-day="' + key + '">' +
                    '<div class="ac-cal-day-num"><span>' + day.getDate() + '</span>' +
                    (dayEvents.length ? '<span class="ac-cal-more">' + dayEvents.length + '</span>' : '') +
                    '</div>';
                shown.forEach(function (e) { html += chip(e); });
                if (dayEvents.length > shown.length) {
                    html += '<span class="ac-cal-more">+' + (dayEvents.length - shown.length) + ' more</span>';
                }
                html += '</div>';
            }
            html += '</div>';
            mount.innerHTML = html;
        }

        function renderWeek() {
            var gridStart = new Date(cursor);
            gridStart.setDate(cursor.getDate() - cursor.getDay());
            var html = '<div class="ac-cal-week">';
            for (var i = 0; i < 7; i++) {
                var day = new Date(gridStart);
                day.setDate(gridStart.getDate() + i);
                var key = toKey(day);
                var cls = 'ac-cal-day' + (key === toKey(today) ? ' is-today' : '');
                html += '<div class="' + cls + '" data-cal-day="' + key + '">' +
                    '<div class="ac-cal-day-num"><span>' + DOW[day.getDay()] + ' ' + day.getDate() +
                    ' ' + MONTHS[day.getMonth()].slice(0, 3) + '</span></div>';
                eventsOnDay(key).forEach(function (e) { html += chip(e); });
                html += '</div>';
            }
            html += '</div>';
            mount.innerHTML = html;
        }

        function renderAgenda() {
            var list = visibleEvents().slice().sort(function (a, b) {
                return a.start < b.start ? -1 : (a.start > b.start ? 1 : 0);
            });
            if (dayFocus) list = list.filter(function (e) {
                return dayFocus >= e.start && dayFocus <= (e.end || e.start);
            });
            if (!list.length) {
                mount.innerHTML = '<div class="ac-empty"><i class="fas fa-calendar-day"></i>' +
                    '<h3>No events</h3><p>Nothing scheduled for this period.</p></div>';
                return;
            }
            var html = '<ul class="ac-agenda">';
            list.forEach(function (e) {
                var d = parseKey(e.start);
                var range = e.end && e.end !== e.start
                    ? d.getDate() + ' ' + MONTHS[d.getMonth()].slice(0, 3) + ' – ' +
                      parseKey(e.end).getDate() + ' ' + MONTHS[parseKey(e.end).getMonth()].slice(0, 3)
                    : d.getDate() + ' ' + MONTHS[d.getMonth()].slice(0, 3);
                html += '<li><div class="ac-agenda-date"><div class="d">' + d.getDate() +
                    '</div><div class="m">' + MONTHS[d.getMonth()].slice(0, 3) + '</div></div>' +
                    '<div style="flex:1;min-width:0">' +
                    '<div class="d-flex align-items-center gap-2">' +
                    '<span class="ac-event-dot type-dot-' + e.type + '"></span>' +
                    '<strong>' + escapeHtml(e.title) + '</strong>' +
                    '<span class="ac-badge bare neutral ms-auto">' + escapeHtml(e.typeLabel) + '</span>' +
                    '</div>' +
                    '<div class="small ac-muted">' + range + '</div>' +
                    (e.description ? '<div class="small ac-muted">' + escapeHtml(e.description) + '</div>' : '') +
                    '</div>' +
                    (canManage && e.editUrl ? '<div><a class="btn btn-sm btn-outline-secondary" href="' +
                        e.editUrl + '"><i class="fas fa-pen"></i></a></div>' : '') +
                    '</li>';
            });
            html += '</ul>';
            mount.innerHTML = html;
        }

        function renderTitle() {
            if (!titleEl) return;
            if (view === 'month') {
                titleEl.textContent = MONTHS[cursor.getMonth()] + ' ' + cursor.getFullYear();
            } else if (view === 'week') {
                var end = new Date(cursor);
                end.setDate(cursor.getDate() - cursor.getDay() + 6);
                titleEl.textContent = cursor.getDate() + ' ' + MONTHS[cursor.getMonth()].slice(0, 3) +
                    ' – ' + end.getDate() + ' ' + MONTHS[end.getMonth()].slice(0, 3) + ' ' + end.getFullYear();
            } else {
                titleEl.textContent = 'Agenda';
            }
        }

        function render() {
            renderTitle();
            if (view === 'month') renderMonth();
            else if (view === 'week') renderWeek();
            else renderAgenda();
        }

        function renderDetail(key) {
            if (!detailEl) return;
            var list = eventsOnDay(key);
            var d = parseKey(key);
            var html = '<div class="ac-card-head"><span class="ac-card-title"><i class="fas fa-calendar-day"></i>' +
                d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear() + '</span>' +
                '<button type="button" class="btn btn-sm btn-outline-secondary" data-cal-detail-close>' +
                '<i class="fas fa-times"></i></button></div><div class="ac-card-body tight">';
            if (!list.length) {
                html += '<p class="ac-muted mb-0">No events on this day.</p>';
            } else {
                list.forEach(function (e) {
                    html += '<div class="d-flex align-items-start gap-2 mb-2">' +
                        '<span class="ac-event-dot type-dot-' + e.type + '" style="margin-top:.4rem"></span>' +
                        '<div><div class="fw-semibold">' + escapeHtml(e.title) + '</div>' +
                        '<div class="small ac-muted">' + escapeHtml(e.typeLabel) + '</div>' +
                        (e.description ? '<div class="small ac-muted">' + escapeHtml(e.description) + '</div>' : '') +
                        '</div></div>';
                });
            }
            html += '</div>';
            detailEl.innerHTML = html;
            detailEl.hidden = false;
        }

        qsa('[data-cal-view]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                view = btn.getAttribute('data-cal-view');
                qsa('[data-cal-view]').forEach(function (b) { b.classList.toggle('active', b === btn); });
                if (view !== 'agenda') { dayFocus = null; if (detailEl) detailEl.hidden = true; }
                render();
            });
        });

        qsa('[data-cal-nav]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var dir = btn.getAttribute('data-cal-nav') === 'prev' ? -1 : 1;
                if (view === 'month') cursor.setMonth(cursor.getMonth() + dir);
                else if (view === 'week') cursor.setDate(cursor.getDate() + dir * 7);
                render();
            });
        });

        var todayBtn = qs('[data-cal-today]');
        if (todayBtn) todayBtn.addEventListener('click', function () {
            cursor = new Date(today.getFullYear(), today.getMonth(), 1);
            dayFocus = null;
            if (detailEl) detailEl.hidden = true;
            render();
        });

        qsa('[data-cal-filter]').forEach(function (btn) {
            var key = btn.getAttribute('data-cal-filter');
            active[key] = true;
            btn.classList.add('active');
            btn.addEventListener('click', function () {
                active[key] = !active[key];
                btn.classList.toggle('active', active[key]);
                render();
                if (dayFocus) renderDetail(dayFocus);
            });
        });

        mount.addEventListener('click', function (evt) {
            var day = evt.target.closest ? evt.target.closest('[data-cal-day]') : null;
            if (!day || evt.target.closest('a')) return;
            dayFocus = day.getAttribute('data-cal-day');
            if (view !== 'agenda') {
                view = 'agenda';
                qsa('[data-cal-view]').forEach(function (b) {
                    b.classList.toggle('active', b.getAttribute('data-cal-view') === 'agenda');
                });
            }
            render();
            renderDetail(dayFocus);
        });

        if (detailEl) {
            detailEl.addEventListener('click', function (evt) {
                if (evt.target.closest('[data-cal-detail-close]')) {
                    detailEl.hidden = true;
                    dayFocus = null;
                }
            });
        }

        render();
    }

    /* ------------------------------------------
       MARK ENTRY
       Live validation + progress. The server
       still does the authoritative validation.
       ------------------------------------------ */
    function initMarkEntry() {
        var form = qs('[data-mark-entry]');
        if (!form) return;
        var inputs = qsa('.ac-score-input', form);
        var progressText = qs('[data-mark-progress-text]', form);
        var progressBar = qs('[data-mark-progress-bar]', form);
        var total = inputs.length;

        function refresh() {
            var entered = 0;
            var invalid = 0;
            inputs.forEach(function (input) {
                var max = parseFloat(input.getAttribute('data-max'));
                var raw = input.value.trim();
                var outOfRange = false;
                if (raw !== '') {
                    var val = parseFloat(raw);
                    if (isNaN(val) || val < 0 || (isFinite(max) && val > max)) outOfRange = true;
                    else entered++;
                }
                input.classList.toggle('is-out-of-range', outOfRange);
                input.classList.toggle('is-invalid', outOfRange);
                if (outOfRange) invalid++;
            });
            if (progressText) progressText.textContent = entered + ' of ' + total + ' entered';
            if (progressBar) progressBar.style.width = total ? (entered / total * 100) + '%' : '0%';
            return invalid;
        }

        inputs.forEach(function (input) {
            input.addEventListener('input', refresh);
            input.addEventListener('change', refresh);
        });

        var markAll = qs('[data-mark-all]', form);
        if (markAll) markAll.addEventListener('click', function () {
            qsa('select[data-mark-status]', form).forEach(function (sel) {
                sel.value = markAll.getAttribute('data-mark-all');
            });
            refresh();
        });

        var clearScores = qs('[data-clear-scores]', form);
        if (clearScores) clearScores.addEventListener('click', function () {
            inputs.forEach(function (input) { input.value = ''; });
            refresh();
        });

        form.addEventListener('submit', function (evt) {
            var invalid = refresh();
            if (invalid) {
                evt.preventDefault();
                toast('error', 'Check the scores',
                    invalid + ' score(s) are outside the allowed range (0–' +
                    (inputs[0] ? inputs[0].getAttribute('data-max') : '') + ').');
            }
        });
        refresh();
    }

    /* ------------------------------------------
       ATTENDANCE bulk helpers
       ------------------------------------------ */
    function initAttendance() {
        var form = qs('[data-attendance]');
        if (!form) return;
        qsa('[data-att-all]', form).forEach(function (btn) {
            btn.addEventListener('click', function () {
                var status = btn.getAttribute('data-att-all');
                qsa('select[data-att-status]', form).forEach(function (sel) { sel.value = status; });
                refreshAttendance(form);
            });
        });
        qsa('select[data-att-status]', form).forEach(function (sel) {
            sel.addEventListener('change', function () { refreshAttendance(form); });
        });
        refreshAttendance(form);
    }

    function refreshAttendance(form) {
        var counts = {};
        qsa('select[data-att-status]', form).forEach(function (sel) {
            counts[sel.value] = (counts[sel.value] || 0) + 1;
        });
        qsa('[data-att-count]', form).forEach(function (el) {
            el.textContent = counts[el.getAttribute('data-att-count')] || 0;
        });
    }

    /* ------------------------------------------
       CLIENT-SIDE TABLE SEARCH
       Progressive enhancement over the
       server-rendered list; filters visible rows.
       ------------------------------------------ */
    function initTableFilters() {
        qsa('[data-table-filter]').forEach(function (input) {
            var table = document.getElementById(input.getAttribute('data-table-filter'));
            if (!table) return;
            input.addEventListener('input', function () {
                var q = input.value.toLowerCase().trim();
                var shown = 0;
                qsa('tbody tr', table).forEach(function (row) {
                    if (row.classList.contains('ac-table-empty')) return;
                    var match = !q || row.textContent.toLowerCase().indexOf(q) !== -1;
                    row.hidden = !match;
                    if (match) shown++;
                });
                var empty = qs('[data-table-empty]', table.parentNode) || qs('[data-table-empty]');
                if (empty) empty.hidden = shown !== 0;
            });
        });
    }

    /* ------------------------------------------
       UNSAVED CHANGES GUARD
       Forms opt in with data-guard-unsaved.
       ------------------------------------------ */
    function initUnsavedGuard() {
        qsa('form[data-guard-unsaved]').forEach(function (form) {
            var dirty = false;
            form.addEventListener('input', function () { dirty = true; });
            form.addEventListener('change', function () { dirty = true; });
            form.addEventListener('submit', function () { dirty = false; });
            window.addEventListener('beforeunload', function (evt) {
                if (!dirty) return;
                evt.preventDefault();
                evt.returnValue = '';
                return '';
            });
        });
    }

    /* ------------------------------------------
       BOOT
       ------------------------------------------ */
    function boot() {
        initCharts();
        initCalendar();
        initMarkEntry();
        initAttendance();
        initTableFilters();
        initUnsavedGuard();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
