# Vehicle Inspection App

A single, self-contained HTML file (`vehicle-inspection-app.html`) for performing vehicle inspections in the browser. No server, no build step, no external dependencies, and it works offline — just open the file.

**Current version: v0.21** · Status: actively iterating (pre-1.0)

> **Maintaining this log:** work-in-progress is collected under **[Unreleased](#unreleased)** at the top of the Changelog. When a version is cut, the Unreleased items become the new `### v0.N` entry (newest first), the version bumps, and the [Current state](#current-state) section is synced to the latest behavior.

## Running it

Open `vehicle-inspection-app.html` in any modern browser (Chrome, Edge, Safari, Firefox).

---

## Current state

### Layout / tabs
- A top **tab bar** switches between two views in the same file: **Inspection** (default) and **Maintenance Scheduler**. Both share the vehicle identity (Vehicle ID + odometer).

### Header / branding
- The form header shows the **Inspectit.app** logo on the left (embedded base64 PNG, self-contained) with the **Vehicle Inspection Form** title and subtitle to its right.

### Vehicle details
- Fields: Unit/Vehicle #, License plate, Make/Model, Odometer mileage, Inspector, and Date (defaults to today).
- Required fields (Vehicle #, Mileage, Date) are validated on save.

### Inspection checklist
- **8 built-in templates** selectable from the Template dropdown: **Auto Inspection** (default), **Van**, **Pickup Truck**, **Commercial**, **Adaptive Auto**, **Adaptive Van**, **Adaptive Pickup Truck**, **Accessible Transit Vehicle**. Auto/Van/Pickup share one category set; the Adaptive variants add adaptive-equipment groups; Commercial and Accessible Transit have expanded commercial groups.
- Auto Inspection categories: Interior · Exterior & Body · Lights & Electrical · Tires & Wheels · Brakes · Engine & Fluids · Belts & Hoses · Suspension · Transmission · Exhaust & Emissions · Safety & Emergency Equipment · Identification & Required Documents.
- Each **main category** name shows in bold black on a blue header bar; sub-category rows sit on white below.
- Each **sub-category** has an **OK / Needs attention / Defect** toggle (defaults to OK), color-coded green/orange/red.
- Setting a sub-category to **Needs attention** or **Defect** reveals an auto-growing, word-wrapping **description** box (orange/red).
- Live summary tallies OK / attention / defect counts.

### Editing the checklist / templates
- **Edit template** mode (toggle in the toolbar) is required for all structural editing. In it: **+ Add category** (end of the checklist), **+ Add sub-category** (end of each category), **🗑 Delete sub-category** (per line), **Delete category** (per category), and **editable category/sub-category names**.
- In **normal (inspection) mode** these are all hidden and names are read-only — the checklist is status toggles + issue notes only, so an inspection can't accidentally change the template.
- The **Edit template** toggle is always available, so any saved template can be loaded and edited again at any time.
- **Templates toolbar:** dropdown of the 8 built-in vehicle-type templates plus any saved customs, **Edit template** (navy button), **Save template…** (green button), **Delete template** (red, edit-mode only). Saved templates persisted in `localStorage`.

### Vehicle diagram (damage marking)
- A single box currently shows a **placeholder** (dashed light-gray panel, "Add a vehicle diagram here later") — the embedded line drawing was removed. A graphic can be re-added by setting `DIAGRAM_SRC` (data URI or image URL) in the script; the canvas then renders it (with optional `CROP` trimming and a threshold-sharpening pass).
- **Click-to-place marking codes:** click an **empty** spot → popup menu (titled *Add marking code*) → pick a code → the letter lands there (red, white halo).
  - Built-in codes: **DM** = Damage · **DT** = Dent · **S** = Scratch · **CR** = Crack · **OX** = Oxidation · **O** = Other
- **Edit or delete a single mark:** click directly **on** an existing mark → popup menu (titled *Change code*) → pick a different code to change it, or **✕ Delete this mark** to remove just that one.
- **Custom codes:** the **+ Code** button (diagram header) or **+ New code…** (bottom of the popup) prompts for an abbreviation + label and adds it to both the popup menu and the legend. Custom codes are session-only (reset on reload).
- **Undo** removes the last code; **Clear all** removes every code.
- The code **key** appears under the diagram on the form and is printed in the PDF report.

### Notes & repair tickets
- Free-text notes box for additional observations.
- **Items Flagged for Repair** list below the notes auto-populates with every sub-category set to **Needs attention** or **Defect** (status tag, item, category, issue description); updates live as toggles change.
- **Issue Repair Ticket** button (enabled once something is flagged) builds a structured `repair_ticket` record (generated ID, vehicle info, reporter, `priority` = `high` when any defect is present, flagged items with severity + description), saves it to `localStorage` (`repairTickets`), and opens a printable ticket (print → *Save as PDF*).

### Saving / output
- **Save Inspection** validates, stores the record in `localStorage`, and opens a formatted printable **PDF report** (auto-launches print → *Save as PDF*). PDF is headed by the **Inspectit.app logo + title**, and includes vehicle details, navy-headed category tables with ✓ marks, red issue descriptions, the marked-up diagram + key, an **Overall Vehicle Condition** box, a **Problems / Conditions Noted** summary, notes, and signature lines.
- **Download JSON** saves the raw inspection record as a `.json` file.

### Saved record (JSON shape)
```json
{
  "type": "vehicle-inspection",
  "template": "Vehicle Inspection Check-Off Sheet",
  "generatedAt": "2026-06-10T...Z",
  "vehicle": { "vehicleId": "", "plate": "", "makeModel": "", "mileage": 0 },
  "inspection": { "inspector": "", "date": "", "notes": "" },
  "items": [
    { "category": "Exterior & Body", "item": "Body / paint condition", "status": "defect", "description": "..." }
  ],
  "summary": { "ok": 0, "attn": 0, "defect": 0, "passed": false },
  "diagramImage": "data:image/png;base64,..."
}
```

### Maintenance Scheduler (second tab)
- **Vehicle bar:** Vehicle ID, current odometer, and an as-of date (prefilled from the inspection's vehicle/mileage the first time the tab is opened). All scheduler state is keyed by Vehicle ID.
- **Schedule** = service groups (categories) of service items (sub-categories), each group with a recommended interval in **months and/or miles**. Built-in **"Standard Maintenance Schedule"**: Every 3 Months (3–5k mi), Every 6 Months (5–7k mi), Annually (12–15k mi), Periodically (30–60k mi), Periodically (60–100k mi).
- **Per item:** Last Service date, Mileage, computed **Next Due** window, and a status badge — **OK / Due soon / Overdue / Not logged** — from current odometer + as-of date vs. the group's interval (whichever of miles/months comes first). **Mark serviced** logs it (date + mileage) and updates the record.
- **Due summary banner** lists what's due/overdue (green when all current) — the offline "notification". A **count badge** also rides on the Maintenance Scheduler tab (red=overdue, orange=due-soon), visible from the Inspection tab.
- **Service Log** per vehicle (Download JSON / Clear).
- **Edit schedule** mode mirrors the inspection editor: rename items/groups, edit each group's months/miles interval, add/remove items and groups; **Save schedule…** stores custom schedules in `localStorage`; dropdown selects built-in or saved schedules.

---

## Changelog

All notable changes per iteration. Newest first. Dates are ISO (YYYY-MM-DD).

### Unreleased
- _(work in progress — nothing pending)_

### v0.21 — 2026-06-22 — Maintenance "due" badge on the Inspection tab
- The **Maintenance Scheduler** tab now shows a **count badge** of items due + overdue for the current vehicle — **red** if any are overdue, **orange** if only due-soon, hidden when nothing is due.
- Visible from the **Inspection** tab too, so pending maintenance is surfaced without switching views. Updates live as the vehicle ID, odometer, last-service entries, or mark-serviced actions change; computed from the maintenance vehicle/odometer, falling back to the inspection vehicle/odometer.

### v0.20 — 2026-06-22 — Maintenance Scheduler (new tab)
- Added a **top tab bar** ("Inspection" / "Maintenance Scheduler") that switches between two views in the same self-contained file. The inspection form is unchanged and remains the default tab.
- New **Maintenance Scheduler** page with the same template/edit/save model as the inspection checklist (categories = service groups, sub-categories = service items), plus:
  - **Vehicle bar:** Vehicle ID, **current odometer**, and an **as-of date** (prefilled from the inspection's vehicle/mileage when first opened).
  - **Built-in "Standard Maintenance Schedule"** with the requested groups & intervals: Every 3 Months (3–5k mi), Every 6 Months (5–7k mi), Annually (12–15k mi), Periodically (30–60k mi), Periodically (60–100k mi).
  - **Per-item rows:** Last Service date, Mileage, computed **Next Due** window, and a color-coded **status** — OK / Due soon / Overdue / Not logged — calculated from current odometer + as-of date vs. each group's recommended **miles and months** interval (whichever comes first).
  - **Due summary banner** at the top listing what's due/overdue (green when all current). This is the "get notified" mechanism — visual, fully offline.
  - **Mark serviced** button per item records it (date + mileage) and appends to a per-vehicle **Service Log** (with Download JSON / Clear).
  - **Edit schedule** mode: rename items & groups, edit each group's months/miles interval, add/remove items and groups; **Save schedule…** stores custom schedules in `localStorage`; built-in + saved schedules selectable from a dropdown.
- All state (service records, logs, saved schedules) persists in `localStorage`, keyed by Vehicle ID.

### v0.19 — 2026-06-22 — Inspection vehicle-type templates
- Renamed the built-in checklist from "Standard Vehicle Inspection" to **Auto Inspection**, and rebuilt its categories/sub-categories per the updated spec (Interior, Exterior & Body, Lights & Electrical, Tires & Wheels, Brakes, Engine & Fluids, Belts & Hoses, Suspension, Transmission, Exhaust & Emissions, Safety & Emergency Equipment, Identification & Required Documents).
- Added **7 more built-in templates** selectable from the Template dropdown: **Van**, **Pickup Truck**, **Commercial**, **Adaptive Auto**, **Adaptive Van**, **Adaptive Pickup Truck**, **Accessible Transit Vehicle**.
  - Van & Pickup Truck reuse the Auto category set.
  - Adaptive Auto/Van/Pickup = Auto categories **plus** adaptive groups (Vision & Visibility Support, Incontinence & Interior Protection, Vehicle Entry & Ramps, Seating & Transfer Aids, Adaptive Hand Driving Controls, Adaptive Steering Assistance, Storage & Mobility Equipment, Emergency Equipment).
  - Commercial has its own expanded set (incl. Coupling Devices, Cargo, Reflective Markings & Visibility, State-Specific Inspection Items); Accessible Transit Vehicle = Commercial **plus** the adaptive groups.
- All templates share the same form, buttons, diagram, save/report flow, and vehicle connection.

### v0.18 — 2026-06-12 — Toolbar colors, removed import/export, diagram placeholder
- **Colored toolbar buttons:** **Edit template** is now filled **navy blue** (brand color) and **Save template…** filled **green** (the "OK" green), so the primary template actions stand out.
- **Removed the Export and Import template buttons** (and the hidden file input + their JSON download/upload handlers). Templates are still saved/loaded via **Save template…** and the Template dropdown (`localStorage`). The inspection **Download JSON** button is unaffected.
- **Removed the embedded vehicle line drawing** (~86 KB base64; file dropped from ~135 KB to ~88 KB). The diagram box now shows a **placeholder** — a dashed light-gray panel reading "Vehicle graphic placeholder / Add a vehicle diagram here later."
- **Damage marking still works** on top of the placeholder. To add a real graphic later, set `DIAGRAM_SRC` (a clearly-commented spot in the script) to a data URI or image URL; `CROP` optionally trims it.

### v0.17 — 2026-06-12 — Editing gated to Edit-template mode
- **+ Add category**, **+ Add sub-category**, and **🗑 Delete sub-category** now appear **only in Edit-template mode** (previously available in normal mode). This reverses the v0.16 "always available" behavior in favor of a clean split.
- **Category and sub-category names are read-only in normal mode** and editable only in Edit-template mode — so an inspection can't accidentally alter the template. (Name fields already styled as plain text outside edit mode; this enforces it functionally.)
- **Normal (inspection) mode** is now purely status toggles + issue notes; **Edit-template mode** is purely structural editing (add/rename/delete categories & sub-categories), with status toggles hidden while editing.
- Unchanged: **Save template…** stores the edited checklist for reuse, and the **Edit template** toggle is always in the toolbar — any saved template can be loaded and edited again at any time.

### v0.16 — 2026-06-12 — Inline "Add category"
- Added an always-available **+ Add category** button at the **end of the checklist** (after the last category), with a dashed separator above it. No longer requires entering Edit-template mode.
- Clicking it appends a new category (one starter item), scrolls to it, and **selects its name** for immediate renaming.
- **Category names are now editable inline** in normal mode (previously read-only unless in Edit-template mode), matching how sub-category names already behave — so a freshly added category can be named without switching modes.
- Removed the old edit-mode-only "+ Add category" button from the static toolbar (replaced by the inline one). Deleting a whole category still lives in **Edit template** mode.

### v0.15 — 2026-06-12 — Logo on the PDF report
- Added the **Inspectit.app** logo to the top of the printed **PDF report**, left of the "Vehicle Inspection Report" title (new `.report-head` flex row, logo 60px tall).
- The report reuses the logo **already embedded in the page header** (read from the DOM at build time) rather than duplicating the base64, so the file size is unchanged and the two stay in sync.

### v0.14 — 2026-06-12 — Branding (Inspectit.app logo)
- Added the **Inspectit.app** logo to the **left side of the form header**, with the "Vehicle Inspection Form" title and subtitle to its right (header is now a flex row; logo 76px tall, shrinking to 58px on screens ≤480px).
- Logo is **embedded as a base64 PNG** (~33 KB, 270×240) so the app stays a single self-contained file that works offline / over `file://`.
- Also set the same logo as the custom icon for the **"Vehicle Inspection" Desktop launcher** app.

### v0.13 — 2026-06-12 — Editable marks, custom codes & Safari fix
- **Delete/change individual marks:** clicking directly on an existing mark now opens the popup titled *Change code* with the code list plus a **✕ Delete this mark** option (removes only that mark, vs. Undo/Clear all). Hit-detection uses a 60px (canvas-space) radius; clicking empty space still adds a new mark (*Add marking code*).
- **Custom marking codes:** added a **+ Code** button in the diagram header and a **+ New code…** entry at the bottom of the popup; both prompt for an abbreviation + label and add the code to the popup menu and the on-form legend. (Session-only — not yet persisted.) The popup menu is now rebuilt dynamically per click rather than once at load.
- **Safari `file://` fix:** wrapped the diagram's `getImageData` (line-sharpening pass) and `toDataURL` (report/JSON snapshot) in `try/catch`. Safari throws a `SecurityError` reading a canvas on `file://` pages, which previously broke the diagram and silently killed **Save / Download / Print** when the file was opened by double-click. These now degrade gracefully (sharpening skipped / diagram image omitted) instead of failing.
- Updated the diagram hint text to explain placing, editing, deleting, and adding codes.

### v0.12 — 2026-06-11 — Flagged items & repair tickets
- Added an **Items Flagged for Repair** list beneath the Notes field that auto-populates with every sub-category currently set to **Needs attention** or **Defect** (status tag, item, category, and its issue description), updating live as toggles change.
- Added an **Issue Repair Ticket** button that assembles a structured `repair_ticket` record (generated ticket ID `RT-<unit>-<base36>`, vehicle info, reporter, `priority` — `high` when any defect is present — and flagged items with severity + description), saves it to `localStorage` (`repairTickets`), and opens a printable ticket (print → *Save as PDF*). Disabled until at least one item is flagged.

### v0.11 — 2026-06-10 — Documentation & versioning
- Added this `vehicle-inspection-app.md` as a running, versioned changelog of every iteration.

### v0.10 — 2026-06-10 — Click-to-place marking codes
- Replaced freehand drawing on the diagram with a **click → popup menu → place letter code** workflow.
- Codes: **DM** Damage, **DT** Dent, **S** Scratch, **CR** Crack, **OX** Oxidation, **O** Other.
- Added **Undo** (remove last) and **Clear all** buttons; **Esc**/click-away dismisses the menu.
- Code **key** shown under the diagram on the form and printed in the PDF report (key-only, plus on-graphic letters).

### v0.9 — 2026-06-10 — Embedded reference artwork & sharper lines
- Replaced the hand-coded vehicle outlines with the supplied **`Car Drawing.jpg`** (Driver/Passenger sides, Front, Rear, Top) embedded as base64 in a single box; cropped in-canvas to the artwork.
- **Sharpened** the lines: high-resolution (supersampled) canvas + black/white threshold pass to remove JPEG haze.

### v0.8 — 2026-06-10 — Vehicle diagram section
- Added a drawable **Vehicle Diagram** section above Notes (pen + clear), captured into the PDF.
- Iterated the artwork: single top view → multi-view (front/side/rear) → **navy header + labeled, bordered cells** matching the PDF style → added a **marking key** (S/D/R/C) → Corolla-style sedan proportions with **driver + passenger (mirrored) side views** → restyled to match a supplied reference JPG (curved silhouette, detailed wheels, pillars, handles, molding).

### v0.7 — 2026-06-10 — PDF report output
- **Save Inspection** now generates a formatted, printable **PDF report** (print-to-PDF, no external libraries) with vehicle details, navy category tables, ✓ marks, issue descriptions, overall condition, problems summary, notes, and signature lines.
- Removed the on-page **JSON** result view (kept the Download JSON button).

### v0.6 — 2026-06-10 — Per-item issue descriptions
- Marking a sub-category **Needs attention**/**Defect** reveals a **description** field (color-coded), saved into the record (`description`).
- Description field **wraps** and **auto-grows**; **Delete sub-category** moved to the far right under the toggles.

### v0.5 — 2026-06-10 — Category styling
- Main category names rendered **bold black on a blue background**; blue applies to the category header only, sub-categories on white. (Fixed an invalid CSS custom-property placement that rendered the bar white.)

### v0.4 — 2026-06-10 — Checklist matches source PDF
- Reorganized the built-in template's categories and sub-categories to mirror the **Vehicle Inspection Check-Off Sheet** PDF (6 categories).

### v0.3 — 2026-06-10 — Sub-category controls & edit fixes
- Per-line **add/delete sub-category**, then consolidated **Add sub-category** to the end of each category with a right-aligned per-line **Delete**.
- Fixed: newly added sub-category names are editable without entering edit mode.

### v0.2 — 2026-06-10 — Template builder
- Add/delete/rename **categories**; **Edit template** mode; **Save / Export / Import / Delete** templates persisted in `localStorage`.

### v0.1 — 2026-06-10 — Initial form
- Self-contained HTML inspection form: vehicle/mileage fields, **OK / Needs attention / Defect** toggles per item, notes box, and a **Save** button that builds a JSON record (also saved to `localStorage`).

---

## Notes & limitations
- The browser may require **pop-ups allowed** for the PDF report tab.
- `diagramImage` is embedded in the JSON/`localStorage`, making those payloads larger.
- Marking codes/marks are captured into the PDF via the canvas image (not stored as structured coordinates).
- **Opening via `file://` in Safari** (double-click): the diagram skips its line-sharpening pass and the saved JSON/report omits the embedded diagram image (Safari blocks canvas reads on `file://`). Serving the file over `http://localhost` restores both. The form, marks, Save/Download/Print all work either way.
- **Custom marking codes are session-only** — they reset when the page is reloaded.

## Possible next steps
- Add the remaining source-sheet header fields (VIN, Department/Location, Mileage IN/OUT, Driver/Operator).
- Number repeated codes (e.g., DT-1, DT-2) and/or list placed markings in the report.
- Confirmation prompt before **Clear all**.
- **Persist custom marking codes** (e.g., in `localStorage`, alongside templates) so they survive reloads.
