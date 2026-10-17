# REDESIGN_SPEC: a simple, self-explanatory site (story first, tool second)

Problem: the site reads like an enterprise console. Visitors (hackathon judges, video viewers) can't tell what the project is without Googling, layouts leave half the screen empty, the header buttons are heavy, the Scale page uses research-paper plots, and Sources is slow and shows raw JSON. Goal: someone who has never heard of a CMDB understands the idea in 60 seconds, then can explore.

Hard deadline: production by **Oct 18, 13:00 IST**. Priorities P0 → P1 → P2; at 12:00 stop starting new work, finish and deploy what's done. No backend or data changes except where a section says so. Keep the existing tokens file as the base, keep accessibility (axe: zero serious), keep all tests passing (update UI tests to the new structure). Work on branch `redesign`; follow CLAUDE.md.

## 1. Naming (P0)

- Product name in the UI: **Change Guardian**. One-line description under it wherever it appears: "An AI agent that checks IT changes against what systems actually do."
- Northwind Health stays, but only as the demo customer: a quiet line "Demo hospital: Northwind Health (fictional)" with the existing tooltip. Never as the product name.
- README title and repo description updated to match ("Change Guardian: an AI agent on FalkorDB that catches what the CMDB misses"). Keep the repo name.

## 2. Design system fixes (P0)

- **Container:** one centred content width (max 1120 px) with a 12-column grid and 24 px gutters on every page. No section uses only the left 60% of the screen: every section either spans the grid or pairs text (columns 1–6) with a visual (columns 7–12).
- **Header:** a light bar. Left: a small graph mark (3 connected dots, SVG, uses `--declared` and `--signal`) + "Change Guardian" in 17 px semibold. Centre/left: navigation (section 3). Right: "Reset demo" as a quiet text button (no dark fill, `--ink-muted`, underline on hover) and the theme toggle as an icon button (sun/moon, with aria-label). The spend and saved-answers line moves to the footer of the agent panel and to "Under the hood"; it does not sit in the header.
- **Type:** keep Atkinson Hyperlegible Next and Mono. Display size 48 px only for the landing headline; page titles 28 px; section titles 21 px; body 17 px on the landing page, 15 px in the tool pages. Mono only for CI names, never for labels or headings.
- **Buttons:** primary = filled `--declared`; secondary = outline with `--rule` border and `--ink` text; tertiary = text link. No other button styles.
- **Spacing rhythm:** landing sections separated by 96 px; tool page sections by 48 px. Remove empty boxes and placeholder panels that have nothing in them.
- **Avoid:** card grids of identical boxes, ALL-CAPS labels, middle-dot meta strings, arrows appended to buttons, decorative gradients, entrance animation on every section.

## 3. Information architecture (P0)

Top navigation, five items, plain names:

1. **How it works** (landing, default)
2. **Review a change** (current Change review)
3. **Findings**
4. **Ask the agent** (Incident and Chat merged, section 6)
5. **Under the hood** (Sources, Scale, evaluation, MCP; section 7)

Old hash routes (#change=, #findings, #incident, #chat, #sources, #scale) keep working by redirecting to the new places.

## 4. Landing page "How it works" (P0)

A scrolling story, one idea per section, each section full grid width. Copy below is final unless it doesn't fit.

**Section 1 — hero** (text columns 1–6, visual columns 7–12, fills the first screen at 1440×900)
- Headline (48 px): "Before you patch a hospital server, see what it really breaks."
- Body: "Change Guardian is an AI agent. It compares what the hospital's IT records say with what its systems actually do, and warns you when a 'safe' change isn't."
- Buttons: "Watch it review a change" (starts the tour), "Try it yourself" (opens Review a change).
- Visual: the headline chain (Emergency care → H03 ED critical results → ORU-CRIT-H03 → lrc-db-01 → dc1-host-07), drawn large enough to fill the column height. **The one orchestrated motion on the site:** on first load (respect reduced motion), the chain first shows "What the records say": the link between ORU-CRIT-H03 and lrc-db-01 is absent and only lab reporting is red. After 1.5 s the dashed red link draws itself in, labelled "found by the agent", and the boxes above turn red; a caption under the visual switches from "What the records say: only lab reporting is affected." to "What actually happens: Emergency departments at 51 hospitals lose critical lab results." A small "Replay" text button repeats it.

**Section 2 — the problem in plain words** (text 1–6, visual 7–12)
- Title: "Hospital IT keeps a map. The map goes out of date."
- Body: "Every hospital keeps a record of its systems and what depends on what, called a CMDB. People approving changes trust it. But systems change every week, and nobody updates the record. A patch that looks harmless on the map can break something nobody knew was connected."
- Visual: two small side-by-side diagrams of the same 5 boxes, "On the map" (one link missing) and "In reality" (the link present, dashed red).

**Section 3 — what the agent does** (a true sequence of 4 steps, laid out horizontally across the grid, each with a small icon and two short lines; numbered because it is a sequence)
1. "Reads the records" — the CMDB.
2. "Checks the evidence" — 11 monitoring and discovery tools.
3. "Walks the graph" — follows every dependency, many steps deep, in FalkorDB.
4. "Decides and acts" — risk, who to warn, a note on the change, a fix for a person to approve.
Under it: one line "And it remembers what people approved or rejected, in its own memory graph."

**Section 4 — watch it work** (visual 1–7, text 8–12)
- Visual: a compact, readable excerpt of the recorded trace (4 steps max, plain-language lines, tool ids small), ending in the verdict "High risk: 51 hospitals affected, 51 hidden links".
- Text: "It chooses its own steps, one tool at a time, and says why. Every number in its answer is checked against the graph." Button: "See the full investigation".

**Section 5 — why a graph** (text 1–5, chart 6–12)
- Title: "Following dependencies is what graphs are built for."
- Body (plain, 2–3 sentences): at 3.5 million components, finding everything that depends on one storage array takes 38 ms in FalkorDB and 357 ms in a tuned PostgreSQL; the honest caveat that PostgreSQL is better at the final reporting step, so the fastest design uses both.
- Chart: a simple horizontal bar chart drawn in SVG with the design tokens (not an image): 3 bars pairs max (Walk the dependencies; Full report; Both together), labelled values, FalkorDB in `--declared`, PostgreSQL in `--ink-muted`. Link "All measurements" → Under the hood.

**Section 6 — proof and try it**
- Three short facts in one row (plain sentences, not big-number tiles): "Tested on 9 scenarios, 4 never seen during development: 7 correct." · "About $0.002 per investigation." · "All data is fictional; the code is open source." (render these as three sentences side by side, no middle dots in the text).
- Three buttons: "Review a change", "Ask the agent", "Look under the hood".
- Footer: GitHub, README, benchmark results, "Built for Graph Hacks 2026 (WeMakeDevs × FalkorDB)".

Remove from the landing page: the long glossary (it moves to Under the hood; tooltips stay everywhere), the "What you can do here" list, the full trace image.

## 5. Tool pages: simpler and self-explanatory (P1)

Every tool page starts with one plain sentence that says what it shows and one "Try this" hint, e.g. Review a change: "Pick a planned change. You'll see what the records say it affects, and what it really affects." Try this: "Open CHG-HEADLINE, then ask the agent to review it."

- **Review a change:** keep the current layout (it works). Make the change list narrower; collapse "Past changes" by default. Hide "Saved AI explanation" behind "Read the agent's written explanation". Show the spend line small at the bottom of the agent panel.
- **Findings:** lead each row with the plain sentence; move the "CMDB update changes only…" fine print into a tooltip on "Proposed action"; default filter "Open"; show the count as "59 open findings".

## 6. Ask the agent (P1)

Merge Incident and Chat into one page.
- One large input: "Describe a problem or ask a question". Under it, example chips in two groups: "Something's wrong" (the incident examples) and "Questions" (the chat chips).
- The page decides the mode from the chosen chip, or from a small two-option control "Investigate a problem / Ask a question" (default: question). Investigations use the existing agent stream and verdict display; questions use the existing chat endpoint.
- Answers show the same verdict and trace styles as Review a change, and always say where the answer came from (graph, saved AI answer, live AI answer).

## 7. Under the hood (P0 for Scale and Sources)

Sub-navigation on the page: Evidence sources · Benchmark · How the agent was tested · Connect an MCP client · Glossary. (Render as tabs or anchor links; not as a middle-dot string.)

- **Evidence sources (fix the slowness):** serve the sources list from a static JSON bundled at build time (no database call), so it renders instantly. Show the 11 sources as a simple pipeline graphic: "11 tools → connectors → one graph". Each source row: plain name, one sentence "In a real hospital this is…", and "See an example". The example drawer shows, top to bottom: "What the tool exported" (the raw record, nicely formatted, max 12 lines, with "Show full record"), and "What Change Guardian learned from it" as a plain sentence (e.g. "ORU-CRIT-H03 writes to lrc-db-01 — seen on 14 of 14 days") followed by the evidence fields as a small key-value list. Raw JSON only behind "Show raw evidence".
- **Benchmark (replace the plots):** remove the matplotlib PNG/SVG charts from the page. Show:
  - one sentence verdict;
  - 3–4 simple horizontal bar comparisons drawn in SVG with tokens (walk the dependencies; full report; both together; slowest answers with 100 users), each with its own one-line caption saying what it means;
  - a clean table "All key numbers" with columns Measure / FalkorDB / PostgreSQL / Winner, the winner as a word, not "better" next to the number; footnote for "measured without hiredis";
  - link to bench/RESULTS.md for the full method and research-style charts.
- **How the agent was tested:** the evaluation table (9 scenarios, held out marked), cost, and the honest note about tuning.
- **Connect an MCP client:** the URL, a copyable config snippet, and the list of the 18 tools with one line each.
- **Glossary:** the existing glossary.

## 8. Tour (P0)

Update the tour to the new structure (landing → Review a change → agent investigation → memory → missing link → fix). Keep: recorded run only, card never covers the spotlight or the verdict.

## 9. Verify and deploy

- Screenshots at 1440 and 390, light and dark, of every page and tour step, saved to docs/screenshots/redesign/. Self-critique each against sections 2–4 (empty half-screens, heavy header, jargon in first sentences) and fix before deploying.
- axe-core: zero serious or critical issues. All tests pass. Preview deploy, check tour and one live agent run, then production by 13:00 IST. Report what was done, what was cut, and send the 1440 screenshots of the landing page, Review a change after an agent run, Ask the agent, and Under the hood → Benchmark.