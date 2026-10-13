# Multi-School Management — Delivery Report (Phase 8)

Project: **Susu Savings & Loans** (Django)
Schools added: **Zemzem Golden Child Academy – Mankarigu** and **Zemzem Golden Child Academy – Lingbinsi**
Date: 2026-10-11

---

## 1. Environment (Windows)

| Item | Value |
|------|-------|
| Python | 3.12.10 |
| Virtual env | `E:\Project\susu_loan_system\.venv` (the old broken `venv/` was left untouched) |
| Dependency manifest | `requirements.txt` (pins unchanged) |
| Local DB | `db.sqlite3` (development) |
| Tests | run under `tests/settings.py` with an in-memory SQLite DB |

### Activate & run (PowerShell)

```powershell
cd E:\Project\susu_loan_system
.\.venv\Scripts\Activate.ps1
# If activation is blocked by policy once:
# Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

python manage.py check
python manage.py migrate
python manage.py runserver
```

If you prefer not to activate: `.\\.venv\\Scripts\\python.exe manage.py migrate`.

---

## 2. Test baseline vs. result

| Stage | Command | Result |
|-------|---------|--------|
| Baseline (before feature) | `pytest tests/ -q` | **164 passed** |
| After feature (Phase 8) | `pytest tests/ -q` | **192 passed** (164 existing + 28 new) |
| After Phase 1 hardening | `pytest tests/ -q` | **217 passed** (164 existing + 53 multi-school) |

New test module: `tests/test_multi_school.py` (isolation, proprietor switching, receipts/exports, dashboards, registration enforcement, cross-school write blocking, search/autocomplete isolation, staff list, management commands).

`python manage.py check` → **0 issues**.

> Note: the full suite takes ~10 minutes, dominated by `tests/test_sms.py` network timeouts (pre-existing).

---

## 3. What was implemented

### 3.1 New app — `apps/schools`
- `School` model (`name`, `short_name`, `code`, `location`, `phone`, `email`, `address`, `is_active`, timestamps, `display_name`).
- `scoping.py` — central isolation helpers:
  - Global roles: `SUPER_ADMIN`, `ADMIN`, `PROPRIETOR` (+ Django superusers).
  - School-bound role: `HEAD_TEACHER` (must have a `school`).
  - Other staff keep their original global behaviour unless a `school` is assigned.
  - `scope_queryset()` / `scope_queryset_by_path()` / `can_access_school()` / `get_active_school()`.
  - Active school stored in session key `active_school_id`.
- `context_processors.school_context` — exposes `active_school`, `switchable_schools`, `is_global_user` to every template.
- `views.switch_school` (POST) + `urls` at `/schools/switch/`.
- Admin registration for `School`.
- Migrations: `0001_initial`, `0002_seed_schools` (idempotent seed of the two branches).
- Management commands:
  - `setup_schools` — creates/refreshes the two schools; with `--with-users` also provisions the proprietor + one head teacher per school (idempotent; warns if a created user has no password).
  - `assign_school_data <CODE> [--dry-run]` — attaches legacy (school-less) records to a branch, parents-first.

### 3.2 Accounts — roles & school binding
- `User.Role` gains `PROPRIETOR` and `HEAD_TEACHER`.
- `User.school` FK (nullable, `SET_NULL`-style safe) + `is_proprietor` / `is_head_teacher` helpers.
- `core.decorators.role_required` grants `PROPRIETOR` wherever `SUPER_ADMIN` is permitted (no per-view churn).
- `StaffCreationForm` can assign a school; admin updated.
- Migration: `accounts/0004_user_school_alter_user_role`.

### 3.3 School fees — per-school data
- New abstract `SchoolOwnedModel` (nullable `school` FK, `on_delete=PROTECT`) applied to
  `SchoolClass`, `AcademicYear`, `Student`, `FeeCategory`, `FeeStructure`,
  `StudentFeeAccount`, `FeePayment`, `ReminderLog`.
- `save()` derives `school` from parents (class → student, year → structure/account, account → payment, student → reminder log).
- Per-school uniqueness: `(school, code)` for classes/categories, `(school, name)` for years.
- **Backward compatible**: `school` is nullable, so pre-existing rows stay valid (`school = NULL`).
- Migration: `school_fees/0002_*` (adds nullable FKs, adjusts `unique_together`, adds an index on `(school, is_active)`).

### 3.4 Views / forms / services / templates
- All school-fees list/detail/CRUD/report/export/pay/reminder views are scoped with `scope_queryset(...)`.
- `HEAD_TEACHER` added to the manager-level role lists (assign fee, reminders, reports, export).
- Forms accept a `school=` kwarg and scope their related querysets. A school-scoped user has the field pinned to their school (it is removed from the form so it cannot be tampered with); a global user in the combined view must choose a school explicitly (the field is required).
- `assign_fee_to_all` and the admin "apply to all students" action are constrained to the selected school (explicit choice in combined view, the branch in a scoped view); a legacy term with no school only ever touches school-less classes, so the operation cannot leak across branches.
- Student CSV importer resolves a `school` column, scopes class lookup, and reports ambiguity across schools.
- Proprietor dashboard (`templates/dashboard/proprietor_dashboard.html`): combined totals + per-school breakdown with a one-click switch; savings/loans summary kept.
- Dashboard routing: `CUSTOMER → customer`, `HEAD_TEACHER → school_fees_dashboard`, `PROPRIETOR → proprietor dashboard`, everyone else → `staff_dashboard` (SUPER_ADMIN routing deliberately unchanged).
- Base template: School Fees nav for head teachers/proprietor, school switcher + active-school badge, staff-only Schools admin link.
- School identification added to: receipt (branch name + School row), fee dashboard header, student & fee-payment lists (School column in the combined view), CSV exports (School column), reminder SMS (`{{school_name}}` → branch name).

### 3.5 Security / safety
- `.env.example` had real Sailup + Paystack keys committed — **sanitized to empty placeholders**. The real `.env` was left untouched.
- No data was deleted; no destructive DB operations were run.
- Pre-existing dangling FK orphans in the dev DB were repaired **non-destructively** (dangling nullable FKs nulled, rows preserved) so SQLite migrations could run. Backup: `%TEMP%\opencode\db.sqlite3.bak_*`.

---

## 4. Roles at a glance

| Role | Scope |
|------|-------|
| `SUPER_ADMIN`, `ADMIN`, `PROPRIETOR` | All schools (combined view); may switch into one |
| `HEAD_TEACHER` | Exactly one school; forced to it, cannot switch |
| Other staff (`MANAGER`, `CASHIER`, `COLLECTOR`, …) | Global unless a `school` is assigned |
| `CUSTOMER` | Unchanged |

Legacy rows with `school = NULL` are visible to global users (proprietor/admin) but hidden from head teachers — no mis-attribution, no data loss.

---

## 5. Provisioning after deployment

```powershell
# 1) Apply migrations
python manage.py migrate

# 2) Create the two schools + staff accounts (idempotent)
python manage.py setup_schools --with-users `
    --proprietor-username proprietor --proprietor-password "<strong-password>" `
    --mankarigu-username mankarigu_head --mankarigu-password "<strong-password>" `
    --lingbinsi-username lingbinsi_head --lingbinsi-password "<strong-password>"

# 3) (Optional) attach any pre-multi-school data to a branch
python manage.py assign_school_data MANKARIGU --dry-run
python manage.py assign_school_data MANKARIGU
python manage.py assign_school_data LINGBINSI
```

Passwords can be provided via `PROPRIETOR_PASSWORD`, `MANKARIGU_HEAD_PASSWORD`, `LINGBINSI_HEAD_PASSWORD`.

---

## 6. Files touched (summary)

- New: `apps/schools/**`, `tests/test_multi_school.py`, `templates/dashboard/proprietor_dashboard.html`, `MULTI_SCHOOL_DELIVERY.md`.
- Updated: `apps/accounts/{models,forms,admin}.py` + migration; `apps/core/decorators.py`; `apps/school_fees/{models,forms,views,admin}.py`, `services/{accounts,reminders}.py` + migration; `apps/dashboard/views.py`; `config/settings/base.py`; `config/urls.py`; several `templates/**`; `tests/settings.py`; `.env.example`; `README.md`.

---

## 7. Known items / recommendations (not blockers)

1. **Pre-existing migration drift (unrelated to this feature):**
   `python manage.py makemigrations --check` reports two metadata-only drifts:
   - `audit`: `AuditLog.action` choices → `audit/0003_*`
   - `loans`: `LoanPolicy` Meta options → `loans/0003_*`

   These are harmless (no schema change) and were present before this work. Generate them only if you want a clean `makemigrations --check`:
   ```powershell
   python manage.py makemigrations audit loans
   ```
2. `ReminderTemplate` remains global by design (shared copy), matching the original permission model; each sent `ReminderLog` is school-tagged.
3. Campaigns intentionally exclude `HEAD_TEACHER` (unchanged `CAMPAIGN_ROLES`).
4. Dev DB has a small amount of unreachable legacy data (`school = NULL`); use `assign_school_data` once its branch is known.

---

## 8. Phase 1 hardening addendum (2026-10-11)

This addendum covers the final Phase 1 pass: server-side school validation, explicit
school choice in the combined view, dashboard metrics, a staff/teacher list, a
read-only health check, and a safe migration-rehearsal path — all without touching
the authoritative database.

### 8.1 What was added

| Area | Change |
|------|--------|
| Registration enforcement | `SchoolClassForm`/`AcademicYearForm`/`FeeCategoryForm` now make `school` **required** in the combined view. `StudentForm`/`FeeStructureForm`/`AssignFeeForm` gained an explicit, required `school` field in the combined view; dependents (`school_class`, `academic_year`, `term`, `fee_category`) are filtered to the chosen school and cross-school combinations are rejected in `clean()`. |
| Scoped users | When a school is in focus the `school` field is removed and the instance is pinned to that school, so a manipulated POST value cannot reassign the record. |
| Server-side guard | New `_school_denied(request, obj)` helper (`apps/school_fees/views.py`) checks `can_access_school` after every create/update save. |
| Assign fees | `assign_fee_to_all(..., school=...)` takes the explicitly chosen school; with no school it only touches school-less classes (never leaks). |
| Dashboards | Proprietor dashboard now reports **students, teachers (head teachers), staff, payments received**, per-school **teacher/staff** columns, and an **unassigned-records review banner**. Aggregates use plain `COUNT`/`SUM` with no join duplication, and a test asserts `combined_* == sum(per_school)`. |
| Staff & teachers | New read-only list at `/schools/staff/` (`school_staff_list`) with search + School column in the combined view; scoped for head teachers, global for proprietor/admin. |
| Health check | New read-only `python manage.py school_status [--list-unassigned]` — per-branch counts/totals, unassigned records flagged for review, and a reconciliation check between per-branch totals and the whole table. |
| Migration rehearsal | `config/settings/rehearsal.py` runs management commands against a **copy** of the DB (`REHEARSAL_DB`) so migrations can be rehearsed without touching `db.sqlite3`. |

### 8.2 Files changed in this pass

- `apps/school_fees/forms.py` — explicit required school + dependent scoping/validation.
- `apps/school_fees/views.py` — `_school_denied` guard on all creates/updates; assign-fee school passing.
- `apps/school_fees/services/accounts.py` — `assign_fee_to_all(school=...)` isolation fix.
- `apps/dashboard/views.py` — `_school_fees_overview` staff/teacher/unassigned metrics.
- `apps/schools/views.py`, `apps/schools/urls.py` — `staff_list` view + `/schools/staff/`.
- `apps/schools/management/commands/school_status.py` — new read-only command.
- `templates/dashboard/proprietor_dashboard.html` — headcount cards, staff/teacher columns, unassigned alert.
- `templates/schools/staff_list.html` — new staff/teacher list page.
- `templates/school_fees/student_form.html`, `templates/school_fees/assign_fee.html` — explicit School field.
- `templates/base.html` — "Staff & Teachers" nav link; selector label "Combined — All Schools".
- `config/settings/rehearsal.py` — new migration-rehearsal settings.
- `tests/test_multi_school.py` — +25 tests.
- `tests/test_school_fees.py` — `test_create_student` updated for the new required-school behaviour.

### 8.3 Migrations

**No new migrations were required by this pass.** `python manage.py makemigrations --check`
reports only the two pre-existing metadata-only drifts (`audit`, `loans`) documented
in §7.1. The multi-school migrations already in the database are:

- `schools/0001_initial`, `schools/0002_seed_schools`
- `accounts/0004_user_school_alter_user_role`
- `school_fees/0002_*`

### 8.4 Tests actually executed

| Command | Result |
|---------|--------|
| `python manage.py check` | **0 issues** |
| `pytest tests/test_multi_school.py -q` | **53 passed** |
| `pytest tests/ -q` | **217 passed**, 54 warnings (pre-existing `staticfiles` dir warning + SMS timeouts) |
| `python manage.py makemigrations --check --dry-run` | only the pre-existing `audit`/`loans` drift |

### 8.5 Data verification (authoritative `db.sqlite3`)

`python manage.py school_status` on a copy of the authoritative database:

```
Zemzem Golden Child Academy – Mankarigu  [MANKARIGU]   Students/classes/etc: 0
Zemzem Golden Child Academy – Lingbinsi  [LINGBINSI]   ... 0
Unassigned (no school) — needs review
  Students: 1   Classes: 3   Academic years: 2   Fee categories: 1
  Fee structures: 1   Fee accounts: 1   Payments: 2   Reminder logs: 3
  Fees expected: 250.00   Collected: 110.00   Outstanding: 140.00
Reconciliation: OK: branch totals reconcile with the whole table.
```

Record counts and financial totals reconcile. The legacy rows have **no reliable
evidence** of which branch they belong to, so they are **flagged for review**
(appear on the proprietor dashboard and in `school_status`) rather than guessed.

### 8.6 Remaining issues / notes

1. **API surface**: the only DRF endpoints are the Susu/loans domain
   (`apps/core/api_views.py`, mounted at `/api/v1/`). School-fees records have **no
   API endpoints**, so the cross-school write vector is form/POST based and is now
   guarded server-side. `CustomerViewSet` and friends are unrelated to schools and
   were left unchanged. (If school-scoped API access is added later, reuse
   `scope_queryset`/`can_access_school`.)
2. Legacy (`school = NULL`) rows remain hidden from head teachers and visible to
   global users; the proprietor dashboard now surfaces a count so they can be
   reviewed and attached with `assign_school_data`.
3. `ReminderTemplate` stays global (§7.2); each `ReminderLog` remains school-tagged.
4. The full test suite still takes ~10 minutes, dominated by `tests/test_sms.py`
   network timeouts (pre-existing).

### 8.7 Safe deployment for the recovered database

The recovered server database is the source of truth. Follow this order; nothing
below flushes, resets, or drops data.

```powershell
# 0) Back up first (timestamped copy, integrity-checked)
Copy-Item db.sqlite3 "backups\db.sqlite3.$(Get-Date -Format yyyyMMdd_HHmmss).bak"

# 1) Rehearse the migration on a throw-away copy (the real DB is untouched)
Copy-Item db.sqlite3 backups\rehearsal.sqlite3
$env:REHEARSAL_DB = "E:\Project\susu_loan_system\backups\rehearsal.sqlite3"
python manage.py migrate    --settings=config.settings.rehearsal
python manage.py school_status --settings=config.settings.rehearsal
Remove-Item E:\Project\susu_loan_system\backups\rehearsal.sqlite3

# 2) Apply migrations to the real database
python manage.py migrate

# 3) Verify counts/totals (read-only; flags anything unassigned)
python manage.py school_status

# 4) Provision users if not already present (idempotent)
python manage.py setup_schools --with-users `
    --proprietor-username proprietor --proprietor-password "<strong-password>" `
    --mankarigu-username mankarigu_head --mankarigu-password "<strong-password>" `
    --lingbinsi-username lingbinsi_head --lingbinsi-password "<strong-password>"

# 5) Only after verifying a branch, attach legacy rows (dry-run first)
python manage.py assign_school_data MANKARIGU --dry-run
python manage.py assign_school_data MANKARIGU
python manage.py assign_school_data LINGBINSI --dry-run
python manage.py assign_school_data LINGBINSI

# 6) Run the test suite against the copy/config before going live
pytest tests/ -q
```

---

## 9. Financial dashboard school-scope repair (Mankarigu records missing)

### 9.1 Symptom

Zemzem Golden Child Academy — Mankarigu already had one student with fee records,
but selecting **Mankarigu** showed an empty financial dashboard (0 students,
GHS 0 expected/collected) while **Combined — All Schools** showed the records.

### 9.2 Root cause

School-fees financial records carry their own `school` foreign key, and every
dashboard/list filters on that column:

* `apps/school_fees/views.py::dashboard` → `scope_queryset(StudentFeeAccount…)`
  and `scope_queryset(FeePayment…)`.
* `apps/dashboard/views.py::_school_fees_overview` → `per_school` uses
  `StudentFeeAccount.objects.filter(school=s)`; the combined aggregate uses
  `scope_queryset(StudentFeeAccount.objects.all(), request)`.

The student `STU-000004` belongs to **Mankarigu**, but its fee account
`FEE-000002`, its two payments (`REC-000002`, `REC-000003`) and fee structure
`#2` were created **before** the student was attached to a branch, so their own
`school` FK was still `NULL`. The combined query returns school-less rows, so the
records appeared there; the per-school query filters `school = <Mankarigu>`, so
they were excluded. The denormalised `school` key had gone stale — nothing in
the query layer was wrong.

### 9.3 Fix

1. **`apps/school_fees/school_links.py`** — `backfill_school_from_ownership()`.
   Fills a `NULL` school **only** from reliable parentage and never guesses:
   student ← class; fee account ← student; payment ← account (then student);
   structure ← class (then academic year); reminder log ← student. Truly
   unowned rows (e.g. the shared academic-year/term/category rows, classes with
   no students) are deliberately left `NULL` for review.
2. **`apps/school_fees/migrations/0003_backfill_school_ownership.py`** — a data
   migration that runs the backfill on `migrate`. Reverse is a no-op (it never
   removes a legitimately derived school). Idempotent.
3. **`apps/school_fees/models.py`** — `Student.save()` now re-derives its own
   school from the class (as before) **and** fills the `NULL` school on its
   existing fee accounts and payments, so a later branch assignment can no
   longer leave financial rows stale.

### 9.4 Records affected (authoritative DB)

| Record | Before | After |
| --- | --- | --- |
| `StudentFeeAccount FEE-000002` | `school = NULL` | `school = MANKARIGU` |
| `FeePayment REC-000002/000003` | `school = NULL` | `school = MANKARIGU` |
| `FeeStructure #2` | `school = NULL` | `school = MANKARIGU` |

Record counts and money were **not** changed: 1 student, 1 account, 2 payments,
GHS 250 expected / GHS 110 collected / GHS 140 outstanding before and after.
Still unassigned (left for review, no reliable owner): classes `P2`, `P3`,
academic years `2025/2026`, `2026/2027`, category `Tuition`.

### 9.5 Verification

* Migration rehearsed on a copy (`config.settings.rehearsal`) before touching the
  authoritative DB; backup taken first
  (`backups/db.sqlite3.20261011_093442.pre_financial_fix.bak`).
* `manage.py school_status --list-unassigned` reports
  **`OK: branch totals reconcile`**; Mankarigu = 1 student, 1 account, 2 payments,
  250/110/140.
* End-to-end HTTP check on the live DB (school-fees dashboard, school selector):
  Combined `(1, 250, 110, 140)`, Mankarigu `(1, 250, 110, 140)`,
  Lingbinsi `(0, 0, 0, 0)`.
* Regression tests: `TestFinancialOwnershipRepair` (backfill, idempotency,
  preservation of receipts/totals, no guessing) and `TestBranchFinancialDashboards`
  (branch shows only its records; combined equals the sum of branches with no
  double counting; head-teacher dashboard totals).

### 9.6 Remaining items

* Shared academic-year / term / fee-category rows remain school-less by design.
  They are usable for the combined/global view and (via the legacy-aware forms)
  when editing; the fee-dashboard year/term **filter** dropdowns stay empty for
  a branch-scoped user until an operator assigns those reference rows. This does
  not affect any metric.
* The Susu savings/loans side of the application has no school concept by
  design; only the school-fees domain is school-scoped.

---

## 10. Academic management module

### 10.1 What was built

A new, additive app (`apps.academics`) layers a full academic module on top of
the existing school-fees primitives. Nothing in the fees, savings or loans domain
was modified; no table was reset, flushed or dropped.

- **Academic configuration:** years/terms are the existing fees primitives;
  subjects, class-subject links, teacher→class/subject assignments, grading
  scales + bands, per-school academic settings, assessment definitions, academic
  calendar (exam periods/deadlines), announcements and terminal-report fields.
- **Attendance:** per class/subject/date sessions with a single dedupe entry
  point, corrections with an audit trail, and per-student / per-class summaries.
- **Marks & results:** teacher mark entry restricted to assigned classes,
  submit → review → approve/return → publish workflow, weighted grade
  calculation from each school's own scale, and class positions.
- **Terminal reports:** on screen, print (CSS) and PDF (reportlab), with
  reconciled fee figures read from the fees module.
- **Dashboards:** a teacher dashboard, a head-teacher academic dashboard, and an
  academic overview card on the proprietor dashboard. Every list/page respects
  the active-school selector.

### 10.2 School isolation & permissions

- Every academic model is school-owned and queried through
  `apps.schools.scoping`; `scope_queryset`/`can_access_school` filter by the
  active school and are enforced server-side on every create/update/delete — the
  Combined selector aggregates without double counting.
- `TEACHER` was added to `User.Role` and to `SCHOOL_BOUND_ROLES`; head teachers
  and teachers are locked to their school. The staff form requires a school for
  both roles, and login routes teachers to the academic dashboard.
- Teachers may only edit classes/subjects they are assigned to via
  `TeacherAssignment`; head teachers/admins may edit any class within their
  school. Approved/published marks are locked; corrections are audited.

### 10.3 Missing / absent / exempt marks

`StudentMark.score = NULL` plus an explicit `status`
(`PRESENT`/`ABSENT`/`EXEMPT`/`INCOMPLETE`) keeps missing marks distinct from a
genuine zero. Grade and average calculations ignore non-present marks unless the
school configures otherwise; a missing mark is never silently treated as zero.

### 10.4 Terminal report & fee reconciliation

Financial figures are read from the existing fees ledger (`StudentFeeAccount`,
`FeePayment`) — no second ledger and no recomputation. Outstanding and credit are
distinguished. Fields the fees module does not model (discounts, scholarships,
waivers, refunds) are shown as **"Not configured in the fees module"** rather
than a misleading amount, and are configurable per school via
`TerminalReportField`.

### 10.5 Files added in this pass

- `apps/academics/` — `models.py`, `forms.py`, `views.py`, `urls.py`, `admin.py`,
  `apps.py`, `migrations/0001_initial.py`,
  `management/commands/seed_academics.py`, and
  `services/{grading,attendance,results,reports,notifications,promotion,access,marks}.py`.
- `templates/academics/*` (dashboards, config, attendance, mark entry,
  submissions/review, results, promote, terminal report, calendar,
  announcements) and a new **Academics** group in `templates/base.html`.
- Integration edits: `config/urls.py`, `config/settings/base.py`,
  `tests/settings.py`, `apps/dashboard/views.py`,
  `apps/accounts/models.py` + `migrations/0005_alter_user_role.py`,
  `apps/accounts/views.py`, `apps/accounts/forms.py`,
  `apps/schools/scoping.py`, `templates/dashboard/proprietor_dashboard.html`.
- `tests/test_academics.py` (new).

### 10.6 Migrations

Two additive migrations, applied to the authoritative database after a
copy-rehearsal:

- `apps/academics/migrations/0001_initial.py` (creates the new academic tables)
- `apps/accounts/migrations/0005_alter_user_role.py` (adds the `TEACHER` choice)

`makemigrations --check` now reports only the two pre-existing metadata-only
drifts (`audit`, `loans`) documented in §7.1.

### 10.7 Tests actually executed

| Command | Result |
|---------|--------|
| `python manage.py check` | **0 issues** |
| `pytest tests/test_academics.py -q` | **23 passed** |
| `pytest tests/test_multi_school.py -q` | **61 passed** |
| `pytest tests/ -q --ignore=tests/test_sms.py` | **226 passed** |

Coverage includes: grading weights, absent/missing (never zero) handling, school
isolation of subjects/assessments/marks, teacher permission enforcement via
direct requests, attendance dedupe/corrections/summary, mark validation,
submit/review/approve/return/lock/publish, terminal-report fee reconciliation
(outstanding vs credit, arrears across terms, unmodelled-field flags), page-render
smoke tests for every academics screen (including the PDF content type), and
dashboard routing.

### 10.8 Data verification (authoritative `db.sqlite3`)

A timestamped backup was taken
(`backups/db.sqlite3.20261011_113131.pre_academics.bak`), the migrations were
rehearsed on a throw-away copy, then applied to the live database. Counts and
money were **identical before and after** (students 2, classes 3, accounts 2,
payments 2, total fees 500, amount paid 110). `seed_academics` created only
additive config: 2 academic settings, 2 grading scales (18 bands) and 20
terminal-report fields across the two schools. No existing fee record was
created, recalculated or duplicated.

### 10.9 Remaining issues / notes

- Subjects are seeded empty by design; each school's subject list, class-subject
  links and teacher assignments are configured by the head teacher in the UI.
- The default grading scale is seeded across the full 0–100 range; schools can
  edit bands/weights/pass marks in *Academics → Grading scales*.
- Discounts/scholarships/waivers/refunds are presentation-only flags until the
  fees module models them; they are never shown as a fabricated amount.
- Head teachers intentionally still land on `school_fees_dashboard`; the academic
  dashboard is reached via the new **Academics** nav group (preserves existing
  behaviour and tests).

