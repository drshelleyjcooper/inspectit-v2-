# Inspectit.app

A single, self-contained HTML file (`inspectit-app.html`) that wraps the vehicle-inspection workflow in a multi-page workspace — login, company profile, a vehicle list, and per-vehicle **Inspection**, **Repair Ticket**, and **Maintenance Scheduler** tools. No server, no build step, no external dependencies; the logo and the full inspection form are embedded, so it works offline — just open the file.

**Current version: v0.21** · Status: actively iterating (pre-1.0)

> The shell's version is **aligned with the embedded inspection app** (`vehicle-inspection-app.html` v0.21), which it mounts inside its Inspection and Maintenance Scheduler tools. Work-in-progress collects under **[Unreleased](#unreleased)**; when a version is cut it becomes the new `### v0.N` entry (newest first) and the [Current state](#current-state) is synced.

> **Note:** the live shell file on disk is named `inspectit-app v18.html` (the "v18" is the old filename, not the current version — the app is v0.21).

## Running it

Open `inspectit-app.html` in any modern browser (Chrome, Edge, Safari, Firefox). Everything you enter is saved in that browser's local storage — see [Notes & limitations](#notes--limitations).

---

## Current state

### Login
- Inspectit.app logo + "Welcome to Inspectit.app", username + password, and a self-contained **security check** (an arithmetic captcha that refreshes).
- **First run** switches to a "create your administrator sign-in" mode (username, password, confirm) and stores the account locally; later runs sign in against it. A session flag keeps you signed in until you log out.

### Home / company profile
- "Welcome to Inspectit.app" with the **company profile** shown as a setup form on first use (Photo optional; **Company Name**, **Supervising Agent**, **Company Identifier** required) or a display card once saved.
- Footer actions: **Edit profile**, **Change password** (modal), **Users** (modal — add/remove approved users, seeded from the sign-in), **Log out**.

### Navigation / shell
- Navy sidebar with the logo and tabs: **Home, Vehicles, Houses, Projects**.
- Every internal page carries the same chrome: a top bar with the page title, an embedded **company-profile chip**, and a **Home** button.
- Responsive: the sidebar collapses to a horizontal top nav on narrow screens.

### Vehicles list + Add Vehicle
- Card grid of vehicles, each with an optional photo, **Vehicle ID / Unit** (title), a color-coded **Type** badge (Auto / Van-Bus / Commercial), plate and make/model, plus **Edit** and **Delete**.
- **Add / Edit** form: Vehicle ID/Unit, License Plate, Make/Model, Type (all required), optional photo.
- Each card has three tool buttons — **Inspection**, **Repair Ticket**, **Maintenance Scheduler** — and summary lines for inspections and repairs (below).

### Inspection page
- Opens the **full embedded inspection form** (vehicle-inspection-app v0.21) inside the page, auto-sized to fit.
- Vehicle ID / plate / make-model are **pre-filled from the card and locked** (shaded, read-only); odometer / inspector / date and the rest stay editable.
- On **Save**: requires the inspector name, then prompts "…cannot be edited once complete…" and "Finalize this inspection report?". On confirm, a summary is filed against the vehicle and a toast confirms it; the form's own **Save-as-PDF** report still runs.

### Inspection history
- The "Last inspection" line on each card opens a per-vehicle **history page**: every saved inspection newest-first with date, result badge (Satisfactory / Attention / Defects), items flagged, inspector, odometer, template, and the OK/attention/defect tally.
- **View report** re-opens the exact v0.21 PDF report from the saved record; **Delete** removes a record; **+ New inspection** jumps into a fresh inspection.

### Repair Ticket page
- Per-vehicle **archive** (newest first) with a **running total cost** and a **cost-range filter** (All time / This month / This quarter / This year), plus **+ New repair ticket**.
- Ticket fields: **Ticket #/ID** (auto, editable), **Date of repair ticket**, **Person recommending repair**, **Vendor**, **Status** (Pending/Complete — a *Date completed* field appears when Complete), **Cost**, **Repair description**, **Notes**, and **Invoices/receipts** uploads (multiple, each downloadable/removable).
- A ticket can be created from scratch or **pre-filled from the vehicle's latest inspection's flagged items**. **Print / Save as PDF** builds a printable ticket. Any ticket can be deleted.
- **Card hand-off:** each vehicle card shows a **Repairs** line with the latest ticket's **#ID** (clickable → opens that ticket), its status and cost, and a "N · $total ›" button to the archive.

### Maintenance Scheduler page
- The per-vehicle **Maintenance Scheduler** tool mounts the embedded app on its **Maintenance tab**: service groups with month/mile intervals, per-item Last Service / Mileage / Next-Due / status (OK / Due soon / Overdue), a due summary banner + tab badge, **Mark serviced** → service log, and editable/savable schedule templates.
- Vehicle identity is locked; the **current odometer is pre-filled from the vehicle's most recent inspection** (editable).

### Houses, Projects
- Placeholder pages with the shared chrome, ready to be built out.

### Data & storage
- Account, company profile, users, vehicles, inspections (summaries + full records), and repair tickets persist in `localStorage` under `inspectit.*` keys, with an in-memory fallback when storage is unavailable.
- Saving inspections and tickets is **defensive**: if storage fills up, heavy data (diagram image / attachments) is shed before the record, so the app never lands in a broken state.

---

## Changelog

All notable changes per iteration. Newest first. Dates are ISO (YYYY-MM-DD).

### Unreleased
- **Houses** and **Projects** sections (still placeholders).
- Optional: hard-lock an inspection after finalize; combined cost/period reporting; cross-device sync (would need a backend).

### v0.21 — 2026-06-22 — Synced embedded app to v0.21 + live Maintenance Scheduler
- **Re-embedded the inspection app at v0.21** (replaced the base64 in `#inspectionDocB64`). The shell's Inspection tool now carries everything from inspection v0.19–v0.21: the 8 **vehicle-type templates** (Auto/Van/Pickup/Commercial/Adaptive ×3/Accessible Transit), the in-form **Maintenance Scheduler tab + due badge**, and the version footer. Shell version bumped v0.18 → **v0.21** to stay aligned.
- **Maintenance Scheduler tool is now live** (was a "coming soon" placeholder). The per-vehicle button mounts the embedded app on its **Maintenance tab**, with the vehicle identity locked and the **current odometer pre-filled from the vehicle's most recent inspection**. New `renderMaintenanceTool()` mirrors the existing inspection-mount pattern (iframe `srcdoc`, auto-fit, ResizeObserver).
- Verified in WebKit: shell loads clean; both the Inspection and Maintenance tools mount the embedded app; schedule renders, vehicle carries over, `buildRecord`/report integration intact.

### v0.18 — 2026-06-15 — Initial Inspectit.app workspace
Built the multi-page shell around the existing inspection form, in stages:
- **App shell:** login (first-run account creation + security check), home page with the required company profile, sidebar nav (Home / Vehicles / Houses / Projects), and shared page chrome (logo, embedded profile chip, Home button). Local-storage persistence with an in-memory fallback.
- **Branding:** embedded the real **Inspectit.app** logo (login lockup, sidebar emblem on a white plate, favicon) and set the brand accent to the logo blue (`#1E64D3`).
- **Vehicles:** card-grid vehicle list with optional photos and a Type badge; Add/Edit Vehicle form (Vehicle ID, plate, make/model, type required); per-vehicle Inspection / Repair Ticket / Maintenance buttons opening a shared sub-page shell.
- **Inspection integration:** mounted the embedded inspection form (vehicle-inspection-app v0.18) inside the Inspection page in an isolated, auto-sized frame, **pre-filling and locking** the vehicle identity.
- **Saved-inspection hand-off:** capture **Save** (require inspector → finalize prompts), file a summary against the vehicle, toast confirmation; the card shows the last inspection.
- **Inspection history:** per-vehicle history page; **re-open any past report** via the form's own report generator; delete records.
- **Repair Ticket page:** full archive with running cost totals + month/quarter/year filter, ticket form (all spec fields incl. status/completion date, vendor, cost, invoice/receipt uploads), create-from-scratch or from inspection flagged items, print-to-PDF, and **#ID/date/cost/status surfaced back onto each vehicle card** with a clickable ID.
- **Version label** aligned to the embedded inspection app (v0.18).

---

## Notes & limitations
- **Data lives in the browser, not the file.** The `.html` is the *app*; vehicles / inspections / tickets save in that browser's local storage. Same file + same browser = data persists; a different device/browser, or cleared browsing data, = a fresh start. Cross-device use would need a backend.
- PDF reports/tickets open in a new tab — the browser may need **pop-ups allowed**.
- Inspection report re-open and repair attachments embed data (diagram images, receipts) in storage, which can grow large; saving sheds the heaviest data first if storage is full.
- An inspection isn't **hard-locked** after "finalize" — the warning is shown, but it can still be reopened and re-saved (adds another history entry).
- Runs as a standalone file; the inspection form and logo are embedded, so no sibling files are required.

## Possible next steps
- **Maintenance Scheduler** — recurring tasks with custom categories (like the inspection templates).
- **Houses** and **Projects** — mirror the Vehicles layout / group work into projects.
- A combined **cost/period report** across tickets; per-vehicle and fleet-wide roll-ups.
- Optional **backend** for accounts and cross-device sync.
