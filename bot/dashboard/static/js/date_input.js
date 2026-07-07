/* date_input.js
 *
 * Replaces bare <input type="date"> for the Patch Planning date fields.
 *
 * Why: <input type="date">'s displayed segment order (mm/dd/yyyy vs
 * dd/mm/yyyy) is controlled entirely by the browser/OS locale, not by
 * this app — so it silently showed US mm/dd/yyyy regardless of the
 * analyst's actual locale (Indonesia uses DD/MM/YYYY). Its per-segment
 * typing UX also made it easy to end up with a garbage year (e.g. "0002")
 * if a keystroke landed in the wrong segment for what the user expected.
 *
 * This component always displays and accepts DD/MM/YYYY text, always
 * submits the same ISO yyyy-mm-dd value the backend already expects (see
 * dashboard/app.py's update_patch_plan route), and never submits a
 * partial or invalid date — the visible text field only writes to the
 * hidden submitted field once the typed date is confirmed valid.
 *
 * No external dependencies (no CDN) — self-contained, consistent with
 * this project's existing static/js files (city_map.js, charts.js) and
 * safer for restricted/air-gapped deployments than pulling in a
 * third-party date-picker library.
 */
(function () {
  "use strict";

  const MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  function daysInMonth(year, month /* 1-12 */) {
    return new Date(year, month, 0).getDate();
  }

  /** "31/03/2026" -> {y:2026,m:3,d:31} if a real calendar date, else null. */
  function parseDMY(text) {
    const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec((text || "").trim());
    if (!m) return null;
    const d = parseInt(m[1], 10);
    const mo = parseInt(m[2], 10);
    const y = parseInt(m[3], 10);
    if (mo < 1 || mo > 12) return null;
    if (y < 1000 || y > 9999) return null;
    if (d < 1 || d > daysInMonth(y, mo)) return null;
    return { y: y, m: mo, d: d };
  }

  function pad2(n) { return String(n).padStart(2, "0"); }

  function toDMY(y, m, d) { return `${pad2(d)}/${pad2(m)}/${y}`; }
  function toISO(y, m, d) { return `${y}-${pad2(m)}-${pad2(d)}`; }

  /** "2026-03-31" -> {y,m,d} (ISO is always well-formed here — it only
   *  ever comes from our own hidden field, populated server-side or by
   *  this same script). */
  function parseISO(text) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((text || "").trim());
    if (!m) return null;
    return { y: parseInt(m[1], 10), m: parseInt(m[2], 10), d: parseInt(m[3], 10) };
  }

  /** Auto-insert '/' separators as the user types digits, capping each
   *  segment (2/2/4 digits) so a stray keystroke can't run past the year
   *  segment and corrupt a later one. Returns the re-masked string. */
  function maskInput(raw, previous) {
    const digitsOnly = raw.replace(/[^\d]/g, "").slice(0, 8); // dd mm yyyy = 8 digits max
    let out = "";
    if (digitsOnly.length > 0) out += digitsOnly.slice(0, 2);
    if (digitsOnly.length > 2) out += "/" + digitsOnly.slice(2, 4);
    if (digitsOnly.length > 4) out += "/" + digitsOnly.slice(4, 8);
    return out;
  }

  class DateField {
    constructor(root) {
      this.root = root;
      this.text = root.querySelector(".date-field-text");
      this.iso = root.querySelector(".date-field-iso");
      this.toggle = root.querySelector(".date-field-toggle");
      this.clearBtn = root.querySelector(".date-field-clear");
      this.popup = root.querySelector(".date-field-calendar");
      this.autosubmit = root.dataset.autosubmit === "true";

      // The ISO hidden field is the source of truth. If the visible text
      // field wasn't already pre-populated (the macro that renders this
      // component does populate it server-side, but this class shouldn't
      // *depend* on that — anything else constructing this markup should
      // still get a correct initial display), derive DD/MM/YYYY from it.
      const initial = parseISO(this.iso.value);
      if (initial && !this.text.value.trim()) {
        this.text.value = toDMY(initial.y, initial.m, initial.d);
      }

      // View month/year the popup calendar is currently showing.
      const viewBase = initial || this._today();
      this.viewYear = viewBase.y;
      this.viewMonth = viewBase.m;

      this._updateClearButton();
      this._bind();
    }

    _today() {
      const t = new Date();
      return { y: t.getFullYear(), m: t.getMonth() + 1, d: t.getDate() };
    }

    _bind() {
      this.text.addEventListener("input", () => {
        const caretWasAtEnd = this.text.selectionStart === this.text.value.length;
        this.text.value = maskInput(this.text.value);
        if (caretWasAtEnd) {
          this.text.selectionStart = this.text.selectionEnd = this.text.value.length;
        }
        this._clearError();
        this._updateClearButton();
      });

      this.text.addEventListener("blur", () => this._commitTyped());
      this.text.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          this._commitTyped();
        } else if (e.key === "Escape") {
          this._closePopup();
        }
      });

      this.toggle.addEventListener("click", (e) => {
        e.preventDefault();
        this.popup.hidden ? this._openPopup() : this._closePopup();
      });

      this.clearBtn.addEventListener("click", (e) => {
        e.preventDefault();
        this.text.value = "";
        this.iso.value = "";
        this._clearError();
        this._updateClearButton();
        this.text.focus();
        this._maybeSubmit();
      });

      document.addEventListener("click", (e) => {
        if (!this.root.contains(e.target)) this._closePopup();
      });
    }

    _updateClearButton() {
      this.clearBtn.hidden = this.text.value.trim() === "";
    }


    _clearError() {
      this.text.classList.remove("date-field-invalid");
      this.root.removeAttribute("title");
    }

    _showError(msg) {
      this.text.classList.add("date-field-invalid");
      this.root.setAttribute("title", msg);
    }

    /** Called on blur/Enter from the typed text field. Empty text clears
     *  the planned date (a legitimate action — "not scheduled yet"). A
     *  non-empty value must parse as a real calendar date or nothing is
     *  submitted and the field is flagged, so a bad date never reaches
     *  the server. */
    _commitTyped() {
      const raw = this.text.value.trim();
      if (raw === "") {
        this.iso.value = "";
        this._clearError();
        this._updateClearButton();
        this._maybeSubmit();
        return;
      }
      const parsed = parseDMY(raw);
      if (!parsed) {
        this._showError("Enter a valid date as DD/MM/YYYY.");
        return;
      }
      this.iso.value = toISO(parsed.y, parsed.m, parsed.d);
      this.text.value = toDMY(parsed.y, parsed.m, parsed.d);
      this._clearError();
      this._updateClearButton();
      this._maybeSubmit();
    }

    _maybeSubmit() {
      if (this.autosubmit) {
        const form = this.root.closest("form");
        if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
      }
    }

    _openPopup() {
      const current = parseISO(this.iso.value);
      if (current) { this.viewYear = current.y; this.viewMonth = current.m; }
      this._renderPopup();
      this.popup.hidden = false;
    }

    _closePopup() {
      this.popup.hidden = true;
    }

    _changeMonth(delta) {
      this.viewMonth += delta;
      if (this.viewMonth > 12) { this.viewMonth = 1; this.viewYear += 1; }
      if (this.viewMonth < 1) { this.viewMonth = 12; this.viewYear -= 1; }
      this._renderPopup();
    }

    _selectDay(y, m, d) {
      this.iso.value = toISO(y, m, d);
      this.text.value = toDMY(y, m, d);
      this._clearError();
      this._updateClearButton();
      this._closePopup();
      this._maybeSubmit();
    }

    _renderPopup() {
      const y = this.viewYear, m = this.viewMonth;
      const selected = parseISO(this.iso.value);
      const today = this._today();

      const firstWeekday = new Date(y, m - 1, 1).getDay(); // 0=Sun
      const totalDays = daysInMonth(y, m);

      let cells = "";
      for (let i = 0; i < firstWeekday; i++) {
        cells += `<span class="date-field-cell date-field-cell-empty"></span>`;
      }
      for (let d = 1; d <= totalDays; d++) {
        const isSelected = selected && selected.y === y && selected.m === m && selected.d === d;
        const isToday = today.y === y && today.m === m && today.d === d;
        const cls = [
          "date-field-cell",
          isSelected ? "date-field-cell-selected" : "",
          (!isSelected && isToday) ? "date-field-cell-today" : "",
        ].filter(Boolean).join(" ");
        cells += `<button type="button" class="${cls}" data-day="${d}">${d}</button>`;
      }

      this.popup.innerHTML = `
        <div class="date-field-cal-header">
          <button type="button" class="date-field-nav" data-nav="-1" aria-label="Previous month">‹</button>
          <span class="date-field-cal-title">${MONTH_NAMES[m - 1]} ${y}</span>
          <button type="button" class="date-field-nav" data-nav="1" aria-label="Next month">›</button>
        </div>
        <div class="date-field-cal-weekdays">
          <span>Su</span><span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span>
        </div>
        <div class="date-field-cal-grid">${cells}</div>
      `;

      this.popup.querySelector('[data-nav="-1"]').addEventListener("click", () => this._changeMonth(-1));
      this.popup.querySelector('[data-nav="1"]').addEventListener("click", () => this._changeMonth(1));
      this.popup.querySelectorAll("[data-day]").forEach((btn) => {
        btn.addEventListener("click", () => this._selectDay(y, m, parseInt(btn.dataset.day, 10)));
      });
    }
  }

  function init() {
    document.querySelectorAll(".date-field").forEach((root) => {
      if (!root.dataset.dateFieldInit) {
        root.dataset.dateFieldInit = "true";
        new DateField(root);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Findings/asset tables can inject new rows without a full page reload
  // in a couple of places elsewhere in this app — re-scan lazily rather
  // than assuming this file is the only thing that ever touches the DOM.
  window.ArgusDateInput = { init: init };
})();
