/* ============================================
   Susu Collection & Loan Management System
   Interactive JavaScript
   ============================================ */

(function () {
    'use strict';

    /* ------------------------------------------
       SIDEBAR TOGGLE
       ------------------------------------------ */
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');
    const toggleBtn = document.getElementById('sidebarToggle');

    function openSidebar() {
        if (!sidebar) return;
        sidebar.classList.add('show');
        if (overlay) overlay.classList.add('active');
        if (toggleBtn) toggleBtn.classList.add('active');
        document.body.style.overflow = 'hidden';
    }

    function closeSidebar() {
        if (!sidebar) return;
        sidebar.classList.remove('show');
        if (overlay) overlay.classList.remove('active');
        if (toggleBtn) toggleBtn.classList.remove('active');
        document.body.style.overflow = '';
    }

    if (toggleBtn) toggleBtn.addEventListener('click', function () {
        sidebar.classList.contains('show') ? closeSidebar() : openSidebar();
    });

    if (overlay) overlay.addEventListener('click', closeSidebar);

    // Close sidebar on Escape
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeSidebar();
    });

    // Close sidebar on resize to desktop
    window.addEventListener('resize', function () {
        if (window.innerWidth >= 992) closeSidebar();
    });

    /* ------------------------------------------
       HIGHLIGHT ACTIVE SIDEBAR LINK
       ------------------------------------------ */
    if (sidebar) {
        const currentPath = window.location.pathname;
        sidebar.querySelectorAll('.nav-link').forEach(function (link) {
            if (link.getAttribute('href') === currentPath) {
                link.classList.add('active');
            }
        });
    }

    /* ------------------------------------------
       TOAST NOTIFICATION SYSTEM
       ------------------------------------------ */
    var toastContainer = document.getElementById('toastContainer');
    var toastCount = 0;

    window.showToast = function (type, title, message, duration) {
        if (!toastContainer) return;
        duration = duration || 5000;
        var id = 'toast-' + (++toastCount);

        var icons = {
            success: 'fa-check',
            error: 'fa-times',
            warning: 'fa-exclamation',
            info: 'fa-info'
        };

        var toast = document.createElement('div');
        toast.className = 'toast-item ' + type;
        toast.id = id;
        toast.innerHTML =
            '<div class="toast-icon"><i class="fas ' + (icons[type] || icons.info) + '"></i></div>' +
            '<div class="toast-body">' +
            '<div class="toast-title">' + (title || '') + '</div>' +
            (message ? '<div class="toast-message">' + message + '</div>' : '') +
            '</div>' +
            '<button class="toast-close" onclick="dismissToast(\'' + id + '\')">&times;</button>';

        toastContainer.appendChild(toast);

        // Auto-dismiss
        setTimeout(function () { dismissToast(id); }, duration);
    };

    window.dismissToast = function (id) {
        var el = document.getElementById(id);
        if (!el) return;
        el.classList.add('removing');
        setTimeout(function () { el.remove(); }, 300);
    };

    // Render Django messages as toasts
    var msgEl = document.getElementById('djangoMessages');
    if (msgEl) {
        try {
            var msgs = JSON.parse(msgEl.getAttribute('data-messages'));
            var toastTitles = { success: 'Success', error: 'Error', warning: 'Warning', info: 'Notice' };
            msgs.forEach(function (m, i) {
                setTimeout(function () {
                    var type = 'info';
                    if (m.tag === 'success') type = 'success';
                    else if (m.tag === 'error' || m.tag === 'danger') type = 'error';
                    else if (m.tag === 'warning') type = 'warning';
                    window.showToast(type, toastTitles[type] || 'Notice', m.text, 6000);
                }, i * 300);
            });
        } catch (e) { /* ignore parse errors */ }
    }

    // Legacy: fade out any raw alert boxes still in DOM
    document.querySelectorAll('.alert-dismissible').forEach(function (alert) {
        setTimeout(function () {
            alert.style.transition = 'opacity 0.3s, max-height 0.3s';
            alert.style.opacity = '0';
            alert.style.maxHeight = '0';
            alert.style.overflow = 'hidden';
            setTimeout(function () { alert.remove(); }, 300);
        }, 4000);
    });

    /* ------------------------------------------
       CONFIRMATION DIALOG
       ------------------------------------------ */
    var confirmOverlay = document.getElementById('confirmOverlay');
    var confirmTitle = document.getElementById('confirmTitle');
    var confirmMessage = document.getElementById('confirmMessage');
    var confirmOk = document.getElementById('confirmOk');
    var confirmCancel = document.getElementById('confirmCancel');
    var confirmIcon = document.getElementById('confirmIcon');
    var confirmCallback = null;

    window.confirmAction = function (opts) {
        if (!confirmOverlay) return Promise.resolve(false);
        return new Promise(function (resolve) {
            confirmTitle.textContent = opts.title || 'Are you sure?';
            confirmMessage.textContent = opts.message || 'This action cannot be undone.';
            if (opts.type) {
                confirmIcon.className = 'confirm-icon ' + opts.type;
                var iconMap = {
                    danger: 'fa-trash-alt',
                    warning: 'fa-exclamation-triangle',
                    success: 'fa-check'
                };
                confirmIcon.innerHTML = '<i class="fas ' + (iconMap[opts.type] || 'fa-question') + '"></i>';
            }
            if (opts.confirmText) confirmOk.textContent = opts.confirmText;
            else confirmOk.textContent = 'Confirm';
            if (opts.cancelText) confirmCancel.textContent = opts.cancelText;
            else confirmCancel.textContent = 'Cancel';

            confirmOverlay.classList.add('active');

            function cleanup(result) {
                confirmOverlay.classList.remove('active');
                confirmOk.removeEventListener('click', onOk);
                confirmCancel.removeEventListener('click', onCancel);
                confirmOverlay.removeEventListener('click', onBg);
                resolve(result);
            }

            function onOk() { cleanup(true); }
            function onCancel() { cleanup(false); }
            function onBg(e) { if (e.target === confirmOverlay) cleanup(false); }

            confirmOk.addEventListener('click', onOk);
            confirmCancel.addEventListener('click', onCancel);
            confirmOverlay.addEventListener('click', onBg);
        });
    };

    /* ------------------------------------------
       BUTTON LOADING STATE
       ------------------------------------------ */
    document.querySelectorAll('form').forEach(function (form) {
        form.addEventListener('submit', function () {
            var btn = form.querySelector('[type="submit"]');
            if (btn && !btn.classList.contains('loading')) {
                btn.classList.add('loading');
                btn.disabled = true;
                // Add spinner
                var spinner = document.createElement('span');
                spinner.className = 'spinner-border spinner-border-sm me-1';
                btn.insertBefore(spinner, btn.firstChild);
            }
        });
    });

    /* ------------------------------------------
       STAGGER ANIMATION
       ------------------------------------------ */
    document.querySelectorAll('.stagger-item').forEach(function (el, i) {
        el.style.animationDelay = (i * 0.05) + 's';
    });

    /* ------------------------------------------
       COUNTER ANIMATION
       ------------------------------------------ */
    window.animateCounter = function (el, target, prefix, suffix, duration) {
        prefix = prefix || '';
        suffix = suffix || '';
        duration = duration || 1200;
        var start = 0;
        var startTime = null;

        function step(timestamp) {
            if (!startTime) startTime = timestamp;
            var progress = Math.min((timestamp - startTime) / duration, 1);
            var eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
            var current = Math.floor(eased * target);
            el.textContent = prefix + current.toLocaleString() + suffix;
            if (progress < 1) requestAnimationFrame(step);
            else el.textContent = prefix + target.toLocaleString() + suffix;
        }

        requestAnimationFrame(step);
    };

    // Animate stat values on load
    document.querySelectorAll('[data-counter]').forEach(function (el) {
        var target = parseFloat(el.getAttribute('data-counter'));
        var prefix = el.getAttribute('data-prefix') || '';
        var suffix = el.getAttribute('data-suffix') || '';
        if (!isNaN(target)) window.animateCounter(el, target, prefix, suffix);
    });

    /* ------------------------------------------
       SMOOTH SCROLL TO SECTION
       ------------------------------------------ */
    document.querySelectorAll('a[href^="#"]').forEach(function (link) {
        link.addEventListener('click', function (e) {
            var target = document.querySelector(this.getAttribute('href'));
            if (target) {
                e.preventDefault();
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    });

    /* ------------------------------------------
       PRINT BUTTON
       ------------------------------------------ */
    window.printPage = function () {
        window.print();
    };

    /* ------------------------------------------
       DELETE FORMS → CONFIRM
       ------------------------------------------ */
    document.querySelectorAll('form[data-confirm]').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var msg = form.getAttribute('data-confirm') || 'Are you sure you want to delete this?';
            window.confirmAction({
                title: 'Confirm Deletion',
                message: msg,
                type: 'danger',
                confirmText: 'Delete'
            }).then(function (confirmed) {
                if (confirmed) form.submit();
            });
        });
    });

    /* ------------------------------------------
       HOVER CARD HIGHLIGHT (tables)
       ------------------------------------------ */
    document.querySelectorAll('.table tbody tr[data-href]').forEach(function (row) {
        row.style.cursor = 'pointer';
        row.addEventListener('click', function () {
            window.location.href = this.getAttribute('data-href');
        });
    });

    /* ------------------------------------------
       BACK TO TOP
       ------------------------------------------ */
    var backToTop = document.createElement('button');
    backToTop.className = 'btn btn-primary btn-sm position-fixed d-none';
    backToTop.style.cssText = 'bottom:1.5rem;right:1.5rem;z-index:1000;border-radius:50%;width:40px;height:40px;display:flex;align-items:center;justify-content:center;box-shadow:var(--shadow-lg);';
    backToTop.innerHTML = '<i class="fas fa-arrow-up"></i>';
    backToTop.setAttribute('aria-label', 'Back to top');
    document.body.appendChild(backToTop);

    window.addEventListener('scroll', function () {
        if (window.scrollY > 300) {
            backToTop.classList.remove('d-none');
        } else {
            backToTop.classList.add('d-none');
        }
    });

    backToTop.addEventListener('click', function () {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    /* ------------------------------------------
       SEARCHABLE SELECT
       ------------------------------------------ */
    window.initSearchableSelect = function (selectId, inputId, resultsId) {
        var select = document.getElementById(selectId);
        var input = document.getElementById(inputId);
        var results = document.getElementById(resultsId);
        if (!select || !input || !results) return;

        // Build selected display
        var selectedWrap = document.createElement('div');
        selectedWrap.className = 'searchable-select-selected';
        var selectedText = document.createElement('span');
        selectedText.className = 'selected-text';
        selectedText.textContent = select.options[select.selectedIndex] ? select.options[select.selectedIndex].text : 'Select...';
        var clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.className = 'selected-clear';
        clearBtn.innerHTML = '<i class="fas fa-times"></i>';
        clearBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            select.selectedIndex = 0;
            selectedText.textContent = select.options[0] ? select.options[0].text : 'Select...';
            input.value = '';
            input.focus();
        });
        selectedWrap.appendChild(selectedText);
        selectedWrap.appendChild(clearBtn);
        select.parentNode.insertBefore(selectedWrap, select);

        selectedWrap.addEventListener('click', function () {
            input.value = '';
            input.focus();
            renderResults('');
            results.classList.add('active');
        });

        function renderResults(query) {
            var q = query.toLowerCase().trim();
            results.innerHTML = '';
            var matched = 0;
            for (var i = 0; i < select.options.length; i++) {
                var opt = select.options[i];
                if (!opt.value) continue;
                var text = opt.text.toLowerCase();
                if (q && text.indexOf(q) === -1) continue;
                matched++;
                var div = document.createElement('div');
                div.className = 'search-option';
                if (opt.value === select.value) div.classList.add('selected');

                // Split text: "CUS-000001 - Kofi Mensah" → code + name
                var parts = opt.text.split(' - ');
                if (parts.length === 2) {
                    div.innerHTML = '<strong>' + escapeHtml(parts[1]) + '</strong> <span class="option-code">' + escapeHtml(parts[0]) + '</span>';
                } else {
                    div.textContent = opt.text;
                }

                div.setAttribute('data-value', opt.value);
                div.addEventListener('click', function () {
                    var val = this.getAttribute('data-value');
                    select.value = val;
                    selectedText.textContent = this.textContent;
                    results.classList.remove('active');
                });
                results.appendChild(div);
            }
            if (!matched) {
                var noRes = document.createElement('div');
                noRes.className = 'search-no-results';
                noRes.textContent = 'No results found';
                results.appendChild(noRes);
            }
        }

        function escapeHtml(str) {
            var div = document.createElement('div');
            div.appendChild(document.createTextNode(str));
            return div.innerHTML;
        }

        input.addEventListener('focus', function () {
            renderResults(this.value);
            results.classList.add('active');
        });

        input.addEventListener('input', function () {
            renderResults(this.value);
            results.classList.add('active');
        });

        document.addEventListener('click', function (e) {
            if (!results.contains(e.target) && e.target !== input && !selectedWrap.contains(e.target)) {
                results.classList.remove('active');
            }
        });
    };

})();
