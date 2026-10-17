# DESIGN_FINAL_SPEC: the last design pass ("clinical blueprint")

The site is clear but looks unfinished: content sits in a centred column and the margins are blank, so it reads as padding. Principle for this pass: **every part of the screen has a job.** The centre column is visibly framed, the margins carry a quiet background drawn from our own subject (a hospital's dependency map), and layouts use their width. This is the final design iteration: production by **08:00 IST Oct 18**; at 07:00 stop starting new items. Branch `design-final`. Follow CLAUDE.md. No content or feature changes except where stated.

## 1. Rewrite the stylesheet first (mandatory)

`ui/app.css` currently holds three generations of rules (original, redesign, styling pass) overriding each other, some with `!important`. Replace it with **one clean stylesheet** organised as: reset → tokens usage → layout primitives → components → pages → responsive. Keep `tokens.css` as the single source of values (add the new tokens below). No `!important` except `[hidden]`. Every rule that existed only to undo an older rule disappears. Visual regressions are checked with screenshots before and after.

## 2. Direction: clinical blueprint

The subject is a map of how hospital systems depend on each other, and the product's point is the one link missing from that map. The design looks like a precise technical drawing of that map:

- **The frame.** On every page, the content column has thin vertical rules on its left and right edges running the full page height (`--frame-line`, 1 px). Sections are separated by horizontal rules spanning the frame. Where a horizontal rule meets a vertical one, a **small square node** (8 px, filled `--surface`, 1 px `--declared` border) marks the intersection, like a vertex in a graph. This is the main structural device.
- **The margins.** Outside the frame, the background is a faint, static dependency network: an SVG pattern of small nodes and straight edges in `--declared` at about 8% opacity (lighter in dark mode), with the **one dashed red link** (`--signal`, about 20% opacity) appearing once in each margin near the hero. Generated deterministically from a seed (same picture every load), drawn once, no animation, hidden below 1,200 px width, removed in high-contrast and forced-colours modes. It must never sit behind text.
- **Palette** (light; give dark equivalents and check contrast): page `#F2F5F8` (cool clinical paper), surface `#FFFFFF`, ink `#16202B`, muted ink `#56616D`, rule `#D3DBE3`, blueprint blue `#2C5A8C` (declared/brand), signal red `#B73228` (observed-only, risk). The red appears only where something is genuinely missing or risky.
- **Type:** keep Atkinson Hyperlegible Next and Mono. Hero headline 60 px, weight 700, letter-spacing −0.025em, line-height 1.05. Section titles 36 px. Body 18 px on the landing page, 15 px in tools. Mono only for CI names. No all-caps labels.
- **Motion:** only the hero's missing-link draw-in (already built) and responses to user actions.

## 3. Layout widths

- Landing page frame: 1,280 px including 48 px inner padding on each side. Content uses the full inner width (no 6/12 columns leaving dead space): two-column sections split 5/7 or 6/6 by content weight.
- Tool pages (Review a change, Findings, Ask the agent, Under the hood): frame 1,440 px with 32 px inner padding, so working pages use the screen.
- Below 1,200 px the margin network disappears and the frame lines sit at the screen edges with 24 px padding; below 720 px the frame lines are hidden.

## 4. Header

Full width, 64 px, white with a bottom rule. The left and right ends align with the frame lines of the current page. Left: logo 28 px + "Change Guardian" 22 px bold. Centre: the five links (15 px), active link underlined 2 px in blueprint blue. Far right: "Reset demo" (quiet text button), then the theme button: 40 px circle with **both icons drawn at 20 px and optically equal** (use a full crescent that fills its 24-unit viewBox like the sun does; check side by side in light and dark).

## 5. Landing page

- **Hero:** no forced full-screen height. 80 px top padding, 96 px bottom. Text (5 columns) and the framed chain diagram (7 columns) are **top-aligned**, so the headline starts at the top of the frame, not in the vertical middle. The diagram panel grows to fill its column. The margin network is most visible here.
- **"Hospital IT keeps a map"** (centred composition): title and a two-sentence paragraph centred above (max 640 px), then the two maps side by side, larger (each about 420 px wide), with a small label between them: "one link missing". The maps are the visual focus.
- **"What the agent does":** four steps across the full frame width, joined by a horizontal line with square nodes at each step number (the same node motif as the frame).
- **"Watch it work":** trace excerpt 7 columns, text 5. Add the caption "4 of 9 steps shown" under the excerpt.
- **"Why a graph":** chart 7 columns, text 5; chart panel padded, bars thicker (24 px), values bold.
- **Proof:** three statements in the frame's full width separated by vertical rules with square nodes, then the buttons.
- **Footer** inside the frame, separated by a rule.

## 6. Tool pages

- **Page header band:** inside the frame, title 36 px + lede + "Try this", followed by a frame rule with nodes. No separate full-bleed tinted band any more (the frame provides structure).
- **Review a change:** a two-column app layout using the full 1,440 frame: change list as a 300 px left column (framed, sticky), the review in the remaining width. The dependency view canvas and legend use that width (legend to the right of the canvas at ≥1,280 px). Tables span the review width.
- **Findings:** filters in one row across the frame; the table full width, with the finding sentence column taking the free space.
- **Ask the agent:** input and mode switch across the full width; chip groups side by side; answers full width below.
- **Under the hood:** sub-navigation as a sticky row at the top of the frame; each section separated by a frame rule with nodes rather than boxed in a card.

## 7. Components

- Panels (diagrams, charts, trace, tables): white surface, 1 px rule, 12 px radius, 24 px padding (tables: cell padding only). No shadows except overlays (drawer, tooltip, tour card, toast).
- Buttons 44 px, 8 px radius; primary filled blueprint blue; secondary outline; hover/active/focus states. Risk labels keep text plus colour.

## 8. Process and checks

1. Screenshots before (1440 and 390, light and dark) of every page.
2. Rewrite CSS (section 1) and confirm nothing visually regressed except intended changes.
3. Apply sections 2–7.
4. Self-critique each page at 1440 against the principle: is any area of the screen blank without a job? Is the hero headline at the top of the frame? Are the icons equal? Fix before moving on.
5. axe-core zero serious issues; all tests pass; the tour works end to end (spotlight positions may need updating).
6. Preview, check, production by 08:00 IST. Send the 1440 light screenshots of the landing page (top and full), Review a change, and the landing page in dark mode.