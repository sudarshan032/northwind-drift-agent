# SITE_SPEC: make the website explain itself, fix chat, make it fast

The site is the entry point for judges and visitors. Today it opens straight into an expert tool, so newcomers can't understand what they're looking at. This pass adds an overview and guided tour, explains every screen in plain words, makes chat useful without spending the free AI budget, and applies the speed fixes. Keep the design system from docs/specs/UI_SPEC.md (tokens, type, spacing, colours, accessibility rules). All tests must keep passing. Commit, push and deploy only as described in section 6.

## 1. Overview page (new default tab, first in the navigation)

Plain language, no jargon without an explanation. Draft copy below; keep its meaning, tighten wording if needed.

**Hero**
- Heading: "Your CMDB says this patch is safe. Your hospital network disagrees."
- Sub-heading: "Northwind drift agent compares what the configuration database claims with what systems actually do, finds the gaps, and shows the real impact of a change before it happens."
- Buttons: "Start the guided tour" (primary), "Go to change review".
- Small note: "All data is fictional: Northwind Health is an invented health system sized like a large US one."

**The problem, in one paragraph**
"Hospitals keep a CMDB, a database of every system and what depends on what. Change approvers trust it to answer 'what breaks if we patch this?'. But it drifts: new connections get added and never recorded. When the record is wrong, a 'low-risk' change can take down systems nobody warned."

**How it works: four steps** (a horizontal sequence on desktop, vertical on mobile, numbered because it is a real sequence)
1. "The record: the CMDB lists systems and the dependencies people declared."
2. "The evidence: eleven discovery sources (scanners, traffic metrics, the interface engine, backup and access logs) show what actually happens."
3. "The gap: both go into one FalkorDB graph on the same components. Wherever the evidence and the record disagree, that is drift."
4. "The decision: change review shows the declared impact next to the real one, explains the hidden links, and proposes fixes that a person approves."

**The headline example** (static SVG diagram drawn with the design tokens, no library)
The vertical chain: Emergency care → H03 ED critical results → ORU-CRIT-H03 interface → (dashed red link, labelled "uses lrc-db-01, not in CMDB, found in interface engine config") → lrc-db-01 database → dc1-host-07 server. Caption: "One missing link. The CMDB thinks patching dc1-host-07 affects lab reporting only. In reality it cuts off critical lab results to the Emergency departments of all 51 hospitals." Button: "See it in change review".

**Why a graph** (three short points, numbers taken from the latest bench/RESULTS.md, with a link to it; only measured numbers, stated honestly)
- Walking dependencies: the measured traversal speed-up of FalkorDB over tuned PostgreSQL at the largest size.
- Steady under load: the measured p95/p99 comparison.
- Using each tool for its job: one sentence on the hybrid finding (graph walks, SQL describes).

**What you can do here** (a short list of the tabs with one line each, linking to them).

**About this demo** (small print): fictional data; AI explanations come from free models and are cached on the public site; approvals are real but the demo resets daily and on "Reset demo"; open-source link to GitHub; link to README and bench/RESULTS.md.

## 2. Guided tour

A tour of 5 steps that drives the real UI (switches tabs, selects the right change, highlights the relevant area with a spotlight and a small card). Next / Back / Exit, keyboard accessible, works on mobile, can be restarted from the Overview.
1. Change review with CHG0031513 selected: "A routine EMR upgrade. The CMDB and the evidence agree, so the impact is known: low risk."
2. Select CHG-HEADLINE: "A security patch on one server. The CMDB says it only touches lab reporting."
3. Highlight the comparison table: "The evidence says otherwise: 51 hospitals' Emergency departments depend on it."
4. Highlight the dashed red link in the dependency view: "This is the missing link: the interface engine shows every critical-results interface stores messages in lrc-db-01, but nobody recorded it."
5. Open the top finding: "The agent proposes adding the 51 missing relationships. Approve it, and the change is re-reviewed with the true impact. Reset demo puts it back."

## 3. Explain every screen

- Each tab starts with a two-line "What you're looking at" intro in plain words, plus a "Show me" link that starts the tour at the relevant step where one exists.
- Glossary tooltips (dotted underline, keyboard focusable) for: CMDB, CI, drift, blast radius, declared vs observed, HL7, interface, service offering, business service, discovery source, change request. One shared glossary file, also rendered as a Glossary section on the Overview.
- Findings: lead with the plain-language sentence; move internal codes (D1, D5...) to a secondary column with a tooltip explaining each type.
- Sources: one line per source explaining in plain words what it would be in a real hospital.
- Scale: a short intro saying what was measured and the main conclusion before the charts.
- Empty and fallback states are written for a visitor, never internal wording such as "no tool evidence was gathered".

## 4. Chat that works without spending the budget

1. **Suggested questions:** 6 clickable chips under the input, all answered by the cached AI on the public site. Examples: "What breaks if dc1-host-07 is patched?", "Why is CHG-HEADLINE high risk?", "Which systems hold patient data without an off-site copy?", "Who should be warned about lrc-db-01?", "What is the biggest single point of failure?", "Which apps are used but not in the CMDB?". Pre-warm them.
2. **Answer common questions without AI:** recognise question patterns (what breaks if X fails / is patched, who owns X, what depends on X, why is change Y risky, show findings of type Z) with simple rules, run the matching graph query, and answer from a template with links to the components. Label these "answered from the graph".
3. **Small live allowance on the public site:** at most 15 live AI answers per day across all visitors (shared counter in FalkorDB), plus 3 per visitor per day (by a hashed IP, never stored raw). Never more than OpenRouter's limits; keep the owner's remaining quota untouched. When the allowance is used up, say so in one friendly sentence and point to the suggested questions.
4. **Small talk and unclear questions:** reply with a short welcome and three example questions, without calling the AI.
5. Keep grounding checks (numbers and CI names validated) for every AI answer.

## 5. Speed

1. **Measure first:** Server-Timing headers on every API endpoint (database, compute, total), a cold-start log line, and a before/after table for: first visit, reload, and each tab, on the live site.
2. **Store answers instead of recomputing:** change reviews, the drift scan result, subgraphs, sources and scale data are computed once and stored in FalkorDB with a version number; recompute only after approve or reset (bump the version).
3. **Edge caching:** `Cache-Control: public, s-maxage=…, stale-while-revalidate=…` on read-only GET endpoints, with the version in the URL or as a key, so most visits never reach Python.
4. **Lighter start:** in public mode do not import agent/LLM libraries unless a live chat answer is needed; lazy-import heavy modules; use hiredis.
5. **Return only what the screen needs:** counts plus the paths actually drawn (the counts-only shape from the benchmark), not every impacted component.
6. **Progressive rendering:** show the overview and the verdict immediately, then fill in tables and graphs; skeletons, not spinners; load other tabs on demand.
Targets on the live site: reload under 1 s, cold first visit under 4 s. Report what was achieved.

## 6. Verify and deploy

- Tests for: overview and tour render; glossary tooltips accessible; chat chips return cached answers; rule-answered questions; allowance counter and per-visitor limit; small-talk reply; Server-Timing present; cache headers on GETs; version bump after approve and reset.
- Accessibility check (axe-core) with zero serious issues on every tab, light and dark, desktop and mobile.
- Screenshots of the Overview and each tour step to docs/screenshots/.
- Commit, push, make a Vercel preview deployment, check it (overview, tour end to end, chat chips, a live question, speed numbers), then promote to production and re-check. Report the before/after speed table and any target missed.