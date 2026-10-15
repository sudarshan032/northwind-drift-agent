# UI redesign plan (UI_SPEC.md)

Before-screenshots: `docs/ui-before/` (every tab at 1440 and 390 px, light). What they show: no grid, a 51-line
fan of crossing edges in the dependency view, big-number tiles, ALL-CAPS labels, middle-dot meta strings, LLM
markdown dumped as raw text (`**` markers, pipe tables), and on 390 px a 5,000-px page where the graph shrinks to
an unreadable thumbnail.

## 1. Files

| File | Holds |
|---|---|
| `ui/tokens.css` | every colour, size, spacing, radius, line, shadow and motion value (light, dark, `data-theme`) |
| `ui/app.css` | components and screens; only `var(--token)` references, no literals |
| `ui/app.js` | state, API calls, rendering, the dependency-view layout (plain SVG, no library) |
| `ui/index.html` | the shell: header, tabs, one section per screen, drawer, toast, tooltip |

No build step. Fonts from Google Fonts (Atkinson Hyperlegible Next 400/600, Atkinson Hyperlegible Mono 400/600)
with system fallbacks. Cytoscape is dropped: a canvas graph cannot be reached by keyboard, and with grouping the
view is small enough for hand-placed SVG, where every node and edge is a focusable element with a tooltip.

## 2. Tokens

Colour, light / dark (dark values from the spec). Contrast measured with the WCAG formula (script in the
self-critique notes); all pass.

| Token | Light | Dark | Checked pairs (light / dark) |
|---|---|---|---|
| `--paper` | `#F5F7F6` | `#141A20` | |
| `--surface` | `#FFFFFF` | `#1B232B` | |
| `--ink` | `#18212B` | `#E7ECEF` | 15.1 paper, 16.3 surface / 14.7, 13.4 |
| `--ink-muted` | `#55606B` | `#A3AEB7` | 6.0 paper, 6.4 surface, 5.3 signal tint / 7.8, 7.0, 6.3 |
| `--rule` | `#D7DDE1` | `#2E3943` | dividers only (not information) |
| `--rule-strong` | `#6B7782` | `#8A97A3` | graph lines and arrowheads: 4.3 paper / 5.9 (needs 3:1) |
| `--declared` | `#2F5D8A` | `#7FA8D4` | 6.4 paper, 6.9 surface / 7.1, 6.4; focus ring |
| `--signal` | `#B8322B` | `#F07068` | 5.5 paper, 6.0 surface, 4.9 tint / 6.0, 5.5, 4.9 |
| `--caution` | `#9A6400` | `#E0A84A` | 4.7 paper, 5.0 surface / 8.2, 7.5 (never on a tint) |
| `--steady` | `#2E6B48` | `#6FBF8E` | 5.9 paper, 6.3 surface / 7.9, 7.2 |
| `--signal-tint` | `#F8E6E4` | `#3A2524` | impacted node fill; ink on it 13.5 / 12.0 |
| `--declared-tint` | `#E6EEF6` | `#1F2D3B` | selected row, active toggle |
| `--on-accent` | `= --surface` | `= --surface` | text on declared/signal fills: 6.9 / 6.4 |

Type: `--font-ui` Atkinson Hyperlegible Next, `--font-id` Atkinson Hyperlegible Mono (CI names, IPs, change
numbers only). Scale `--t-13 --t-15 --t-17 --t-21 --t-28 --t-40` (15 body, 40 the verdict only). Weights 400 /
600. Line height 1.5 body, 1.2 headings. `--measure: 75ch` for prose.

Space `--s-4 … --s-64` (4, 8, 12, 16, 24, 32, 48, 64). Radius `--r-control: 6px`, `--r-panel: 10px`,
`--r-none: 0` (graph canvas, tables). `--line: 1px`, `--line-bold: 3px` (changed node), `--focus: 2px`. One
shadow `--shadow-overlay` (drawer, tooltip, toast). Motion `--motion: 180ms`, zero under
`prefers-reduced-motion`.

## 3. Wireframes

### Header (one line)
```
Northwind drift agent  [Fictional data]        Change review  Findings  Incident  Sources  Chat
                                          AI: nemotron-3-super-120b-a12b, 11 of 50 free requests left   [Dark theme]
```
"Fictional data" is a button-styled badge with a tooltip (hover and focus) explaining Northwind Health.

### Change review
```
+-- Upcoming changes ---+--------------------------------------------------------------+
| CHG-HEADLINE   High   | CHG-HEADLINE  Apply ESXi security patch and reboot dc1-host-07|
| dc1-host-07           | Sat 17 Oct, 15:30 GMT+5:30          [High risk]               |
| Sat 17 Oct            |                                                              |
|-----------------------| This change reaches 51 hospitals.                            |
| CHG0031513     Low    | The CMDB knows about none of them.            (40 px)        |
| emr-app ...           |                                                              |
|-----------------------| Impact          CMDB says   Reality   Missing                 |
| Past changes          | Hospitals             0        51        51  (signal)         |
| CHG0031455   Medium   | ...                                                          |
+-----------------------+ Dependency view   (Reality | CMDB says)                        |
                        | tier gutter | SVG canvas                      | legend          |
                        | Hidden dependencies (table: from, to, source, days, last)   |
                        | Who to warn (table: CI, CMDB owner, observed owner)         |
                        | AI explanation (or Rule-based explanation + notice)         |
                        +--------------------------------------------------------------+
```
The change list is a `<ul>` of buttons (`aria-current` on the selected one), grouped "Upcoming changes" and
"Past changes". Selecting loads the review; no Review button. 390 px: the list becomes a horizontal scroller
above the review; the graph becomes a vertical list of the impact path.

### Dependency view
Tier gutter on the left with the eight tier names; nodes are rectangles (radius 0 like the canvas, mono label
truncated to fit, full name in the tooltip); groups are wider rectangles with a count badge, e.g. "51 interfaces
(ORU-CRIT-*)". Edges are cubic curves from the parent's bottom to the child's top, arrowhead at the child. Group
expansion opens a scrollable member list under the canvas (keyboard: Enter on the group).

### Findings
```
Filters: Severity [All]  Drift type [All]  Source [All]  Action [All]        59 findings
| Severity  | Finding                                   | Affected CI   | Sources        | Action       | Status |
| Critical  | Undocumented dependency: 51 interfaces... | lrc-db-01     | interface eng. | CMDB update  | Open   |
```
Row click / Enter opens the right drawer: explanation, evidence table, path diagram, proposed action (before/after
diff for CMDB updates, ticket preview for tickets), buttons "Approve CMDB update" or "Create ticket", and
"Reject". Toast repeats the verb: "CMDB updated" / "Ticket created" / "Finding rejected".

### Incident
Input (placeholder = sample symptom), "Investigate". Result: an `<ol>` of steps (what was checked, tool,
what it found), then "Conclusion" with the answer and the cited path; a one-sentence state line for step-limit or
fallback.

### Sources
Table (source, what it reveals, stands in for, raw format, records, window, "View sample"). The sample opens
in the drawer at double width: raw record (mono, scrollable) beside the evidence row.

### Chat
One column at 75ch: question, answer, "Used" list of CI and path links. A CI link opens the dependency view of
that CI failing (`#ci=name`); a path link opens it with the path highlighted.

## 4. Components

Header, tab bar (`role=tablist`, arrow keys), change list, risk label (text + colour), verdict, comparison table,
segmented toggle (`aria-pressed`), dependency canvas, legend, tooltip (hover and focus), data table, filter
selects, chips (sources), drawer (`role=dialog`, focus trap, Esc closes, returns focus), toast (`role=status`),
skeleton rows, inline notice, error banner, path diagram, before/after diff, markdown-lite renderer for AI text
(bold, lists, headings, tables; HTML escaped).

## 5. API additions needed by the screens

- `GET /changes` adds `risk` per change (rule-based, cached until an approval or rejection).
- `POST /changes/{id}/review` adds `hidden_dependencies` (from, to, evidence source, days seen, last seen) and
  draws the whole radius (every CI in the declared or real radius, edges from the impact layer mapped to their
  CMDB / evidence edges) so the UI can collapse the 51 siblings into one group.
- `GET /components/{name}/review`: the same review for a CI failing (chat links).
- `GET /findings/{id}` adds `diff` (field, before, after) for CMDB updates.
- Incident and chat steps add `found` (one-line result of each tool call) and the answer's `cis`.
- Bug fix found on the way: a change whose target has no impact edges (e.g. a network device) crashed the review
  (`radius` returned not-found for an existing CI).

## 6. Review against the avoid-list (section 2), and what changed

| Avoid | First draft | Revision |
|---|---|---|
| Near-black + acid accent | spec dark palette | kept; dark paper is blue-grey `#141A20`, accents are the same two meaning colours, no extra accent |
| Identical rounded cards with the same shadow | every block in a 10-px card | only the review column and drawer are panels; tables and the canvas are flat (radius 0) with rules; the change list is a plain list with dividers; no shadows except the drawer/tooltip/toast |
| Big-number tiles | KPI strip above the verdict | removed; the comparison table carries the numbers, the verdict sentence carries the message |
| ALL-CAPS labels | uppercase table headers | sentence-case headers in `--ink-muted`, weight 600 |
| Middle-dot meta strings | "51 interfaces · ORU-CRIT-*", "CHG · target · date" | "51 interfaces (ORU-CRIT-*)"; change rows use separate lines for target and date; header status uses a comma |
| Arrows appended to buttons | "Open finding →" | plain verbs: "Investigate", "View sample", "Approve CMDB update" |
| Decorative gradients | none planned | none |
| Fade-in on every section | skeleton shimmer | static skeleton blocks (no animation); motion only for drawer open and path highlight |

## 7. Self-critique notes

Each round: screenshots at 1440 and 390 px, light and dark (Playwright, Chromium), checked against UI_SPEC
(spacing scale, type scale, contrast, label overlap, hierarchy), fixed, repeated.

**Change review, round 1 (1440, light).** The 51 crossing lines are gone: grouping turns the headline into eight
boxes. Found: the observed-only edge from the interface group to `lrc-db-01` ran straight through `lis-reporting`
(it skips the Application tier). Fix: every tier-skipping edge gets a placeholder slot in each tier it crosses
(the standard layered-layout dummy node), so it routes around boxes. Its label was then hidden behind a box; fix:
the slot that carries the label is as wide as the label. The AI explanation was 3,000 px of raw markdown; fix: a
small escaped markdown renderer (bold, lists, headings, tables) and a 320-px clamp with "Show the full
explanation". First version of the clamp used a fade-out gradient; removed (avoid-list: decorative gradients).

**Change review, round 2 (390).** The page was 432 px wide (grid track sized by the comparison table). Fix:
`minmax(0, 1fr)` tracks and `min-width: 0` on grid items; long identifiers wrap. Tables became unreadable four-column
grids; fix: below 960 px every data table stacks into label/value rows (`data-label`). The tab bar wrapped "Change
review" onto two lines; fix: no-wrap tabs in a scrollable bar. The graph becomes the impact path as a list (cause
first, each hop with its relationship, observed-only hops in red). Hidden dependencies show 3 rows on phones, 8 on
desktop, with "Show all 51". A screen-reader-only header escaped the table's scroll container and widened the page;
fix: the table is the positioning context.

**Findings, sources, drawer (1440 and 390).** Row headers rendered as small muted labels; fix: row headers in ink at
body size. "View sample" wrapped; fix: controls never wrap. Moving from a finding to a source by link left the
finding drawer open; fix: changing tab closes the drawer. The raw-record pane centred its text vertically; fix:
sections align to the top. The drawer showed 12 evidence rows before the proposed action; cut to 6 so the diff and
the approve button are close. The group list repeated "observed only" 51 times; now one sentence when all members
share it, per row only when mixed. Findings on phones show severity, finding, CI, action and status in a compact
two-column row (sources and the explanation line are in the drawer).

**Dark.** Same layout; all text pairs re-measured (section 2). Count badges use `--on-accent` on `--signal`
(5.5:1). Impacted fill `#3A2524` keeps ink at 12:1.

**Checks.** Type: only the six scale sizes (13 to 40) appear; weights 400/600; sentence case throughout. Spacing:
`grep` finds no px, hex or rgb literal in `app.css` or `app.js`; the one exception is the 960-px media query,
because CSS cannot read a custom property inside a media condition. Labels: node labels are truncated from the
measured mono character width (13 px minimum) with the full name in the tooltip; tiers never hold more than 8
boxes (the rest fold into "N more"), so labels cannot overlap.

**Accessibility (axe-core 4.10.2).** 9 views (both changes, findings, finding drawer, incident result, sources,
source sample, chat answer, CI view) x light/dark x 1440/390 = 36 runs, plus the expanded group list: 0 violations
of any impact. Issues found and fixed on the way: `aria-label` on SVG `<g>` without a role (serious) -> boxes and
lines are `role="img"`, groups `role="button"` with `aria-expanded`; scrollable drawer body and group list not
focusable (serious) -> `tabindex="0"` with a label; `<header>` inside the drawer and group panel counted as extra
banner landmarks, `<aside role="dialog">` not allowed, tooltip outside landmarks (moderate/minor) -> plain
elements inside `main`. Keyboard (Playwright): Tab reaches the skip link, the badge (tooltip on focus), the tabs
(arrow keys, Home, End move between them), the theme toggle, every change, the Reality / CMDB says toggle, every
graph box (top tier first) and every line, "Show all", and the explanation toggle; Enter on a group opens its
list; Enter on a finding row opens the drawer with focus on Close, Tab is trapped in the drawer, Esc closes it and
returns focus to the row. Focus ring: 2 px `--declared`, offset 2 px (SVG boxes draw the same ring as a stroke).

**Demo check (1440).** Recorded with Playwright: `docs/ui-after/demo.webm` and stills `demo-1` to `demo-6`.
CHG0031513 (low, "The CMDB matches the evidence for this change. Impact is known: 231 components.") ->
CHG-HEADLINE (high, verdict, table, eight-box chain) -> the 51-interface group opened -> top finding drawer ->
"Approve CMDB update" -> toast "CMDB updated" -> CHG-HEADLINE re-reviews as Low risk, "The CMDB matches the evidence
for this change. Impact is known: 107 components." Every step is readable at 1440 x 900 without zooming.
