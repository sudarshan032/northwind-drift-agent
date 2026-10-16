# UI_SPEC: redesign of the Northwind drift agent UI

The backend is done. This pass changes only the UI (`ui/`), plus small API additions if a screen genuinely needs data the API doesn't return yet. Work on branch `ui-redesign` from the current branch. Run rules: RUN_PROMPT.md sections 3 and 9. All existing tests must keep passing; never push. Read `/mnt/skills/public/frontend-design/SKILL.md` if available in your environment; otherwise follow this file.

## 1. The brief

- **Who uses it:** a change approver or integration engineer at a hospital system, and hackathon judges watching a 2-minute video.
- **Its one job:** answer "is this change safe, and if not, what is the CMDB missing and who do I warn?" in under 10 seconds.
- **Current problems:** low contrast, text dumped without spacing, no layout grid, no hierarchy, no design system, a graph too dense to read.

## 2. Design direction

**Concept: "two records of the same hospital."** Every screen compares what the CMDB *declares* with what the evidence *shows*. The design gives those two a consistent visual identity everywhere: declared = steady blue, evidence that contradicts the CMDB = signal red. Everything else stays quiet and neutral so those two colours carry the meaning.

**The one memorable element:** the change verdict, a single plain sentence in large type at the top of the change review, e.g. *"This change reaches 51 hospitals. The CMDB knows about none of them."* It is generated from the computed counts (never from the LLM). Everything around it is disciplined and calm.

**Avoid** (generic AI-dashboard tells): near-black background with one acid accent; identical rounded cards with the same shadow; big-number-plus-small-label tiles; ALL-CAPS labels; meta strings joined with middle dots ("A · B · C"); arrows appended to button text; decorative gradients; fade-in animation on every section.

## 3. Design tokens

Define all tokens as CSS custom properties on `:root`, with dark-mode values under `@media (prefers-color-scheme: dark)` and a manual toggle via `data-theme`. No colour, size or spacing literal outside the token file.

**Colour (light):**
| Token | Hex | Use |
|---|---|---|
| `--paper` | `#F5F7F6` | page background |
| `--surface` | `#FFFFFF` | panels, tables |
| `--ink` | `#18212B` | primary text |
| `--ink-muted` | `#55606B` | secondary text (must still pass 4.5:1 on paper and surface) |
| `--rule` | `#D7DDE1` | borders, dividers |
| `--declared` | `#2F5D8A` | CMDB / declared |
| `--signal` | `#B8322B` | observed-only, impacted, high risk |
| `--caution` | `#9A6400` | medium risk |
| `--steady` | `#2E6B48` | low risk, confirmed |

Dark mode: derive equivalents with the same roles (e.g. paper `#141A20`, surface `#1B232B`, ink `#E7ECEF`, ink-muted `#A3AEB7`, rule `#2E3943`, declared `#7FA8D4`, signal `#F07068`, caution `#E0A84A`, steady `#6FBF8E`). Verify every text/background pair meets WCAG AA (4.5:1 body, 3:1 large text and graph lines).

**Never rely on colour alone:** risk always has a text label; observed-only edges are dashed; stale edges dotted; failover edges dash-dot. Colour-blind users must get the same information.

**Type:** Atkinson Hyperlegible Next (UI text) and Atkinson Hyperlegible Mono (CI identifiers such as `dc1-host-07`, IPs, change numbers; identifiers only, never for labels or prose), from Google Fonts, with system fallbacks. The family was designed for legibility, which suits a clinical-operations tool.
- Scale (≈1.25): 13, 15 (body), 17, 21, 28, 40 (verdict only).
- Weights: 400 body, 600 headings and emphasis. No italics for emphasis.
- Line length ≤ 75 characters for prose; line-height 1.5 body, 1.2 headings.
- Sentence case everywhere. No all-caps labels.

**Spacing:** 4-px base scale: 4, 8, 12, 16, 24, 32, 48, 64. Section gaps 32–48; within a panel 16–24.

**Shape:** radius 6 for controls, 10 for panels, 0 for the graph canvas and tables (hierarchy through shape, not one radius on everything). Borders (`--rule`, 1 px) instead of shadows; at most one subtle shadow, for overlays (drawers, tooltips).

**Motion:** only in response to the user (opening a drawer, expanding a group, highlighting a path), 150–200 ms. Respect `prefers-reduced-motion`. No entrance animations.

## 4. Layout

Max content width 1440 px; 12-column grid with 24-px gutters. Left-aligned text. Responsive down to 390 px (the graph becomes a vertical list of the impact path on small screens).

**Header (one line, quiet):** product name; a small "Fictional data" badge with a tooltip explaining Northwind Health; LLM status (model name and "N of 50 free requests left today", or "AI explanations off"); light/dark toggle. Navigation as text tabs: Change review, Findings, Incident, Sources, Chat.

### 4.1 Change review (the demo opener, highest priority)

```
+----------------------+--------------------------------------------------+
| Upcoming changes     | CHG-HEADLINE  Apply ESXi patch to dc1-host-07    |
| (list, risk label,   | Fri 17 Oct, 03:00 Pacific    [High risk]         |
|  date, target)       |                                                  |
|                      | This change reaches 51 hospitals.                |
| > CHG-HEADLINE  High | The CMDB knows about none of them.   (40 px)     |
|   CHG0031513    Low  |                                                  |
|   ...                | Comparison table                                 |
|                      |            CMDB says  Reality  Missing           |
|                      | Hospitals      0        51       51              |
|                      | Departments    0        51       51              |
|                      | ...                                              |
|                      |                                                  |
|                      | Dependency view [Reality | CMDB says]  legend    |
|                      | (tiered graph, see 4.2)                          |
|                      |                                                  |
|                      | Hidden dependencies (list, each with source)     |
|                      | Who to warn (declared owner vs observed owner)   |
|                      | AI explanation (clearly marked; rule-based       |
|                      |  fallback labelled as such)                      |
+----------------------+--------------------------------------------------+
```

- Left: a list of changes (not a dropdown), each row showing change number (mono), target, date and a risk label. Selecting a row loads the review; no separate "Review" button.
- The comparison is a **table**, not big-number tiles. The "Missing" column is in `--signal` when non-zero. Rows: hospitals, departments, interfaces, service offerings, business services, components.
- Low-risk changes get the same layout with a calm verdict: *"The CMDB matches the evidence for this change. Impact is known: 231 components."*

### 4.2 Dependency view (readability is the point)

- Tiers top to bottom with tier names in a left gutter: Business service, Service offering, Interface, Application, Database, Virtual machine, Server, Storage array. CMDB direction (parent → child).
- **Collapse siblings:** when more than 5 nodes of the same class share the same parent and child, show one group node, e.g. "51 interfaces · ORU-CRIT-*" with a count badge; click to expand into a scrollable list. This turns the headline from 51 crossing lines into a 6-node chain everyone can read.
- Impact by fill: changed component has a thick `--ink` outline; impacted nodes filled with a light tint of `--signal` and a `--signal` border; unaffected nodes neutral.
- Edges: solid `--rule`-dark = in CMDB and confirmed; dashed `--signal` = observed only, not in CMDB, always labelled ("depends on, observed only"); dotted = in CMDB, no evidence; dash-dot `--declared` = failover. Other edge labels appear on hover.
- Hover or focus on a node or edge: a tooltip with name, class, layer(s), and for evidence the source, counts, days seen, first/last seen.
- Legend always visible beside the graph, same order as the edge list above.
- Toggle "Reality / CMDB says" switches which edges drive the red fill.
- Node labels never overlap: truncate with full name in the tooltip; minimum 12-px label size.

### 4.3 Findings

- A table: severity (label + colour), finding in plain language, affected CI (mono), sources (small text chips), proposed action ("CMDB update" or "Ticket"), status. Filters for severity, drift type, source and action. Sorted by severity.
- Selecting a row opens a right-side drawer: explanation, evidence table, a small path diagram, and the proposed action: for CMDB updates, a before/after diff of the fields/relationships; for tickets, a preview of the ticket. Buttons use exact verbs: "Approve CMDB update" / "Create ticket" and "Reject"; the confirmation toast repeats the same verb ("CMDB updated", "Ticket created").

### 4.4 Incident

- One input with a sample symptom as placeholder; "Investigate" button.
- Investigation shown as a numbered sequence of steps (it is a real sequence): what the agent checked, which query/tool, what it found. Then the conclusion, with the shared dependency, related change and path. Step-limit or fallback states explained in one plain sentence.

### 4.5 Sources

- Table: source, what it reveals, real-world tool it stands in for, raw format, records, time window. "View sample" opens a side-by-side panel: raw record (mono, scrollable) next to the evidence row it produced.

### 4.6 Chat

- Simple conversation column (≤ 75 ch), answers show the CIs and paths they used as links into the dependency view.

### 4.7 Empty, loading and error states

- Loading: skeleton rows in the table and graph area, no spinners over the whole page.
- Errors say what happened and what to do ("FalkorDB is not reachable. Start it with `docker compose up`."). No apologies.
- Free-tier limit: "AI explanation unavailable (free-tier limit). Showing the rule-based explanation." inline, not as a modal.

## 5. Copy rules

Plain verbs, sentence case, no filler. Name things the way an IT operations person would: "Upcoming changes", "Hidden dependencies", "Who to warn", "Approve CMDB update", "Create ticket". Avoid system words in the UI (no "IMPACTS_REAL", "layer=evidence"; show "Reality" and "CMDB says"). Dates in the user's local time with the zone shown.

## 6. Process (mandatory)

1. **Before coding:** take screenshots of the current UI at 1440 px and 390 px (Playwright or equivalent) for every tab; save to `docs/screenshots/before/`.
2. **Plan:** write `docs/design/ui-plan.md` with the tokens, wireframes per screen and the components list; review it against section 2's avoid-list and revise anything generic, noting what changed.
3. **Build:** tokens file first, then shared components (header, tabs, table, drawer, tooltip, badges, legend), then screens in priority order: change review, findings, sources, incident, chat.
4. **Self-critique loop:** after each screen, screenshot at 1440 and 390, in light and dark, check against this spec (spacing scale, type scale, contrast, label overlap, hierarchy), fix, repeat. Keep notes in `docs/design/ui-plan.md`.
5. **Accessibility check:** run axe-core (or equivalent) on every tab; zero serious or critical issues. Keyboard: every action reachable, visible focus ring (`--declared`, 2 px, offset 2 px).
6. **Demo check:** record the flow CHG0031513 (low) → CHG-HEADLINE (high) → expand the 51-interface group → open the top finding → approve; the whole story must be readable at 1440 px without zooming.

## 7. Deliverables

- Redesigned `ui/` (no build step required; keep loading libraries as pinned scripts from cdnjs/jsDelivr).
- `docs/design/ui-plan.md`, `docs/screenshots/before/`, `docs/screenshots/` (all tabs, light and dark, 1440 and 390).
- README screenshots replaced with the new ones.
- REPORT.md "UI redesign" section: what changed, accessibility results, anything not done.