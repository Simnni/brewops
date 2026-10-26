# Implementation plan: dashboard date-range filter (ticket 005)

Ticket: `tickets/005-date-filter.md` — "Let people pick a date range and have the numbers and charts follow."

You are implementing this from scratch with no prior context. Everything you need is below.
Do the steps **in order**. Run `uv run pytest` after steps 1, 2, 3 and 5.

## 0. Background (read first)

BrewOps = FastAPI + SQLite + vanilla JS (no build step, no npm, no external URLs).
Run: `uv run seed` (loads sample CSVs, data is 2026-04 .. 2026-07), `uv run start`, open http://localhost:8123.

Data flow today (all unfiltered, "since the beginning of time"):

| Layer | File | What it does |
|---|---|---|
| SQL | `src/brewops/db/queries.py` `get_stats(conn)` | 3 queries on `brew_events`: total count, per-drink (LEFT JOIN from `drink_types`), per-day (`GROUP BY DATE(timestamp)`) |
| SQL | `src/brewops/db/queries.py` `get_machine_health(conn, machine_id)` | per-machine card: brew count + `last_brew`, specialty drink (+ tie check), last maintenance, recent errors |
| API | `src/brewops/api/main.py` `stats()` -> `GET /api/stats`; `machine_health()` -> `GET /api/machines/{machine_id}` | no query params today |
| JS | `src/brewops/frontend/app.js` `loadDashboard()` | fetches `/api/stats` (tiles, `renderDrinkBars`, `renderTimeline`), then `/api/machines` and `/api/machines/{id}` for each machine (`renderMachineCards`) |
| HTML | `src/brewops/frontend/index.html` | tiles `#total-brews`, `#brews-today`, `#machine-count`; `#drink-bars`; `<svg id="timeline">`; `#machine-cards` |

Facts that matter:
- Timestamps are stored as naive local-time text `YYYY-MM-DD HH:MM:SS`. Do **no** timezone conversion.
- `loadDashboard()` is also called after every form submit (`submitForm`). It must therefore read the *current* filter, not assume "all".
- The "brews on last active day" tile (`#brews-today`) = `count` of the **last element of `stats.per_day`**. Keep that logic, using the API's sparse `per_day` (do this *before* zero-filling).
- Existing tests call `get_stats(conn)`, `/api/stats` and `/api/machines/1` with no filter. They must pass unchanged: all new parameters are optional and the default is "no filter".

## 1. Backend queries: `src/brewops/db/queries.py`

### 1a. New helper (put it above `get_stats`)

```python
def _range_clause(column: str, start: str | None, end: str | None) -> tuple[str, list[str]]:
    """Return (conditions, params) limiting `column` to [start, end] inclusive by calendar day.
    start/end are 'YYYY-MM-DD' or None. conditions is '' when neither is given."""
```

Behaviour:
- `start` given -> condition `{column} >= ?` with param `start` (lexical compare works: `'2026-06-01 08:00:00' >= '2026-06-01'`).
- `end` given -> condition `{column} < ?` with param = **end + 1 day** as `'YYYY-MM-DD'`
  (compute with `date.fromisoformat(end) + timedelta(days=1)`; import `date`, `timedelta` from `datetime`).
  This makes the end day inclusive of every time up to 23:59:59 and keeps the `idx_brew_events_timestamp` index usable.
  **Do not** use `DATE(timestamp) <= ?`.
- Return the conditions joined with `" AND "` (e.g. `"be.timestamp >= ? AND be.timestamp < ?"`) and the params list. Return `""` / `[]` when neither is given. The helper returns **only the conditions**; each caller adds its own `WHERE` / `AND` / `ON ... AND` prefix and only when the string is non-empty.

### 1b. `get_stats(conn, start: str | None = None, end: str | None = None)`

Keep the return shape `{"total_brews", "per_drink", "per_day"}` exactly. Apply the range to all three queries:

1. total: `SELECT COUNT(*) AS n FROM brew_events` + `WHERE <cond>` only if cond is non-empty.
2. per_drink: the condition **must go into the `LEFT JOIN ... ON be.drink_type = dt.name AND <cond>`**, NOT into `WHERE`. If it went into `WHERE`, the LEFT JOIN would behave like an inner join and drinks with 0 brews in the range would vanish from the bars. Use `be.timestamp` as the column.
3. per_day: `WHERE <cond>` before `GROUP BY DATE(timestamp)`. Stays sparse (only days that have brews); zero-filling happens in the frontend.

Use `?` placeholders and pass params in order. Never build SQL by interpolating the date strings.

### 1c. `get_machine_health(conn, machine_id, start=None, end=None)`

- Split the current `COUNT(*), MAX(timestamp)` query into two:
  - `brew_count`: `SELECT COUNT(*) FROM brew_events WHERE machine_id = ? [AND <cond>]` (**filtered**).
  - `last_brew`: `SELECT MAX(timestamp) FROM brew_events WHERE machine_id = ?` (**all-time, unfiltered**).
- Specialty drink: the `top_drink` query **and** the `tie_check` query must both get `AND <cond>` (same params, appended after `machine_id`; the tie-check also has the `HAVING COUNT(be.id) = ?` param, so mind the order: machine_id, range params, top_count). If only one is filtered, `is_tied` will be wrong. The existing `if brews["count"] > 0` guard must use the filtered count, so `specialty_drink` is `None` when the machine has no brews in range.
- `last_maintenance` and `recent_errors` stay **unfiltered** (all-time). Do not touch them.
- Return shape unchanged (`brew_count`, `last_brew`, `specialty_drink`, `last_maintenance`, `recent_errors`, plus machine fields).

## 2. API: `src/brewops/api/main.py`

New optional query parameters, same names on both endpoints:

| Param | Type | Format | Meaning |
|---|---|---|---|
| `start` | `datetime.date \| None` | `YYYY-MM-DD` | first day included |
| `end` | `datetime.date \| None` | `YYYY-MM-DD` | last day included (the whole day) |

- Change `from datetime import datetime` to `from datetime import date, datetime`.
- `stats(start: date | None = None, end: date | None = None, conn=Depends(get_db))` -> `queries.get_stats(conn, start.isoformat() if start else None, end.isoformat() if end else None)`.
- `machine_health(machine_id: int, start: date | None = None, end: date | None = None, conn=...)` -> pass the same through to `queries.get_machine_health`. Keep the existing 404 for unknown machines.
- Validation (add a small helper, e.g. `check_range(start, end)`, used by both endpoints):
  - malformed date (`?start=banana`, `?start=2026-13-40`) -> FastAPI returns **422** automatically because of the `date` type. Do not catch it.
  - `start > end` -> `raise HTTPException(400, "start must not be after end")`.
  - `start == end` is valid (a single day).
  - Only one bound given is valid (open-ended range).
- Do **not** change `/api/machines` (the list), the POST endpoints, or `parse_timestamp`.
- Examples:
  - `GET /api/stats` -> unchanged (all time)
  - `GET /api/stats?start=2026-06-01&end=2026-06-07`
  - `GET /api/stats?start=2026-06-01` (from then on)
  - `GET /api/machines/1?start=2026-06-01&end=2026-06-07`

## 3. Test client: `tests/asgi_client.py`

`request(app, method, path, body)` currently hard-codes `"query_string": b""` and uses `path` as given, so `request(app, "GET", "/api/stats?start=...")` would not parse the query. Fix: at the top of `request`, split once on `?`:

```python
path, _, query = path.partition("?")
```

and use `query.encode()` for `"query_string"`, `path` for `"path"`, `path.encode()` for `"raw_path"`. Existing calls (no `?`) behave identically.

## 4. Frontend

### 4a. `src/brewops/frontend/index.html`

Insert a filter bar as the **first child of `<section id="dashboard">`**, before `<div class="stat-tiles">`:

```html
<div class="panel filter-bar" id="date-filter">
  <label for="filter-start">From</label>
  <input type="date" id="filter-start">
  <label for="filter-end">To</label>
  <input type="date" id="filter-end">
  <div class="presets" role="group" aria-label="Quick ranges">
    <button type="button" id="filter-7d">Last 7 days</button>
    <button type="button" id="filter-30d">Last 30 days</button>
    <button type="button" id="filter-all" class="active">All time</button>
  </div>
  <p id="filter-message" class="message" role="status"></p>
</div>
```

Also:
- give the two tile labels ids so their text can change: `<span class="tile-label" id="total-label">brews total</span>` and `<span class="tile-label" id="today-label">brews on last active day</span>`;
- add `<p id="timeline-empty" class="empty-note" hidden>No brews in this range.</p>` inside the "Brews over time" panel, after the `<svg>`.

Use native `<input type="date">` only. No date-picker library, no CDN (`tests/test_frontend.py::test_frontend_has_no_external_resources` forbids `http://` / `https://` in frontend files).

Default state on page load: both inputs **empty** = "All time" (same as today's behaviour), "All time" button has class `active`.

### 4b. `src/brewops/frontend/style.css`

`#dashboard` is a CSS grid with **explicit rows**: `.stat-tiles` row 1, timeline panel row 2, drink-bars panel row 2, machine-cards panel row 3. Put the filter bar in row 1 and shift the others down by one:
- `.filter-bar { grid-column: 1 / -1; grid-row: 1; display: flex; flex-wrap: wrap; align-items: center; gap: .6rem 1rem; }`
- `.stat-tiles` -> `grid-row: 2`; the timeline and drink-bars panels -> `grid-row: 3`; the machine-cards panel -> `grid-row: 4`.
- Style `.filter-bar label` like `.form-panel label`, `.filter-bar input[type=date]` like `.form-panel input` (border `var(--line)`, radius `var(--radius-sm)`, brass focus ring). Preset buttons: small outline buttons using `--brass`; `.presets button.active` filled with the brass gradient (same as `.form-panel button`). `.filter-bar .message { grid-column: auto; flex-basis: 100%; }` (the base `.message` sets `grid-column: 2`, override it here).
- Add `.empty-note` (centered, `var(--ink-soft)`, serif italic).
- Keep `test_static_assets_served` passing (`bar-fill` must remain in the CSS).

### 4c. `src/brewops/frontend/app.js`

1. **Helpers** (new, at the top of the "dashboard" section):
   - `function getFilter()` -> `{start, end}` read from `#filter-start` / `#filter-end` values (empty string -> `null`).
   - `function filterQuery(f)` -> `""` or `"?start=...&end=..."` (only include set keys; use `URLSearchParams`).
   - `function localDateString(d)` -> `YYYY-MM-DD` from the **local** date parts (`getFullYear`, `getMonth()+1`, `getDate()`, zero-padded). Do **not** use `toISOString()` (UTC shift gives the wrong day around midnight; `localNow()` shows how to avoid this).
2. **Presets**:
   - `#filter-7d`: end = today, start = today - 6 days (7 days inclusive). `#filter-30d`: start = today - 29 days. `#filter-all`: clear both inputs.
   - Each sets the inputs, moves the `active` class to the clicked button, then calls `loadDashboard()`.
   - Changing a date input manually removes `active` from all presets (custom range) and calls `loadDashboard()` (use the `change` event).
3. **From after to**: before any fetch, if both are set and `start > end` (string compare is fine for `YYYY-MM-DD`): show `"From" must not be after "To".` in `#filter-message` (class `message error`), **do not fetch**, and leave the previous numbers on screen. Clear the message on a valid range. Also set `min`/`max` attributes (`#filter-end.min = start`, `#filter-start.max = end`) as a hint. The server's 400 is the backstop.
4. **`loadDashboard()`**:
   - Read `getFilter()`, validate as above, build `q = filterQuery(f)`.
   - `fetchJSON("/api/stats" + q)` and `fetchJSON("/api/machines/" + m.id + q)` for each machine. `fetchJSON("/api/machines")` stays unchanged.
   - Stale-response guard: a module-level `let loadSeq = 0;`, `const mySeq = ++loadSeq;` at the start, and `if (mySeq !== loadSeq) return;` after each `await`, so a slow older response cannot overwrite a newer one.
   - Tiles: `#total-brews` = `stats.total_brews`. `#brews-today` = count of the last element of the **sparse** `stats.per_day` (0 if empty). Labels (`#total-label`, `#today-label`): no filter set -> "brews total" / "brews on last active day"; any bound set -> "brews in range" / "brews on last active day in range".
   - Call `renderTimeline(fillDays(stats.per_day, f.start, f.end))`.
5. **Zero-filling days** — new `function fillDays(perDay, start, end)`:
   - `from = start || perDay[0]?.day`, `to = end || perDay[perDay.length - 1]?.day`. If either is undefined (no data and an open bound) return `[]`.
   - Walk day by day from `from` to `to` inclusive, emit `{day, count}` using a `Map` built from the sparse data, `count: 0` for missing days. Do the date arithmetic on **local** dates (`new Date(y, m - 1, d)`, `setDate(getDate() + 1)`, format with `localDateString`). Do not parse `YYYY-MM-DD` with `new Date("2026-06-01")` (that is UTC).
   - Safety cap: if the span exceeds ~1000 days, skip filling and return the sparse data.
6. **`renderTimeline(perDay)`**:
   - Empty array -> clear the svg, show `#timeline-empty` (remove `hidden`) and return. Otherwise set `hidden` on it.
   - `const max = Math.max(1, ...counts)` so an all-zero range does not produce `NaN`.
   - Zero-count days render as zero-height bars (keep the `<title>` tooltip `"{day}: 0 brews"`). A single-day range gives one full-width bar; acceptable.
7. **`renderDrinkBars`**: no change (it already guards `max` with `Math.max(1, ...)`; zero-count drinks still render because the API returns them).
8. **`renderMachineCards`**: no change needed for correctness. The count now means "brews in the selected range", "last ..." stays all-time. Optional: when a filter is active, show `${m.brew_count} brews in range · last ...`.
9. **`submitForm`**: unchanged. It already calls `loadDashboard()`, which now reads the current filter. A brew logged outside the selected range correctly does not change the numbers.
10. Wire the listeners in a new `setupFilter()` called next to `setupForms()` at the bottom of the file. The existing `loadDashboard()` call at the bottom runs once with empty inputs = all time.

## 5. Tests

Run with `uv run pytest`. Reuse the existing fixtures (`conn` in `tests/test_db.py`; `db` in `tests/test_api.py`, which has brews on 2026-06-01 (2x espresso, machine 1) and 2026-06-02 (1x latte, machine 2), and a descale on machine 1 on 2026-06-03).

`tests/test_db.py` (add):
- `get_stats` with `start` and `end` -> only in-range brews counted (total, per_drink, per_day).
- `end` is inclusive of the whole day: a brew at `2026-06-02 23:59:59` is included with `end="2026-06-02"`; one at `2026-06-03 00:00:00` is not.
- `start` only; `end` only; `start == end` (single day).
- Range with no brews: `total_brews == 0`, `per_day == []`, and `per_drink` **still has all 6 drinks with count 0** (regression test for LEFT JOIN `ON` vs `WHERE`).
- `get_machine_health` with a range: `brew_count` and `specialty_drink` reflect the range; `last_brew` is still the all-time max; `last_maintenance`/`recent_errors` unaffected; a range with no brews gives `brew_count == 0` and `specialty_drink is None`.
- Tie detection inside a range (e.g. all-time espresso wins 3-1, but in range espresso 1 / latte 1 -> `is_tied is True`).

`tests/test_api.py` (add):
- `GET /api/stats?start=2026-06-02&end=2026-06-02` -> total 1, per_day `{"2026-06-02": 1}`.
- `GET /api/stats?start=2026-07-01&end=2026-07-31` -> 200, total 0, `per_day == []`.
- `GET /api/stats?start=2026-06-05&end=2026-06-01` -> **400**.
- `GET /api/stats?start=banana` -> **422**.
- `GET /api/machines/1?start=2026-06-02&end=2026-06-02` -> `brew_count == 0`, `last_maintenance` still present.
- Existing tests unchanged and passing.

`tests/test_frontend.py` (add): `index.html` contains `id="filter-start"`, `id="filter-end"`, `id="filter-7d"`, `id="filter-30d"`, `id="filter-all"`; `app.js` contains `filterQuery` and `/api/stats`. Keep the no-external-resources test green.

## 6. Edge cases (all must behave as described)

| Case | Expected behaviour |
|---|---|
| No dates chosen | Identical to today (all time); labels "brews total" / "brews on last active day" |
| Empty range (valid, no brews) | API 200; tiles show 0; drink bars list every drink with 0; timeline shows "No brews in this range." (or all-zero bars when both bounds are set; either is fine as long as there is no `NaN`, no blank and no console error); machine cards show 0 brews and no specialty line, with last brew/maintenance still shown |
| From after to | Frontend: inline error, no request sent, old numbers stay. API: 400 `start must not be after end` |
| Only `start` or only `end` | Valid open-ended range; zero-fill uses the first/last day with data for the open side |
| Single day (`start == end`) | One bar; end day inclusive up to 23:59:59 |
| Days with no brews inside the range | Appear in the timeline as zero-height bars with tooltip "`YYYY-MM-DD`: 0 brews" (continuous x-axis) |
| Seed data is Apr-Jul 2026 and today is later | "Last 7/30 days" presets may legitimately be empty; must show the empty state and not break |
| Malformed date in URL | 422 from FastAPI (not caught) |
| Rapid changes of the date inputs | Last change wins (stale-response guard) |
| Logging a brew while a filter is active | Dashboard reloads with the same filter |
| Future end date | Allowed, no error (just no data there) |

## 7. Verification

1. `uv run pytest` — all green, including the new tests.
2. `uv run seed` then `uv run start` (http://localhost:8123). With the API:
   - `curl "http://localhost:8123/api/stats"` -> totals identical to before the change.
   - `curl "http://localhost:8123/api/stats?start=2026-06-01&end=2026-06-07"` -> `total_brews` equals the sum of the `per_drink` counts and the sum of the `per_day` counts; every `per_day.day` is within the range.
   - `...?start=2026-06-07&end=2026-06-01` -> HTTP 400; `...?start=banana` -> HTTP 422.
   - `curl "http://localhost:8123/api/machines/1?start=2026-06-01&end=2026-06-07"` -> `brew_count` is the in-range count; `last_brew` is the all-time latest.
3. In the browser:
   - Click "Last 30 days", "Last 7 days", "All time": numbers, bars, timeline and machine cards change; "All time" returns to the original numbers.
   - Pick a custom range in June 2026: the timeline has one bar slot per day, including empty days; labels say "in range".
   - Pick a range with no data (e.g. 2025): all zeros, no console errors.
   - Set From later than To: error message appears and nothing is fetched (browser Network tab).
   - Log a brew with a filter active: the dashboard refreshes within the same filter.
4. `git diff --stat` should only touch: `queries.py`, `main.py`, `app.js`, `index.html`, `style.css`, `tests/asgi_client.py`, the three test files, and optionally `README.md`.

## 8. Docs

The README has no API section; optionally add one line (e.g. under "Getting started": "The dashboard can be filtered by date range"). Nothing else to update.

## Out of scope

Filtering maintenance events / errors / `last_brew`; time-of-day filtering; per-machine or per-drink filtering; persisting the filter (URL, localStorage); any DB schema change (no migration needed).
