# Live site speed (SITE_SPEC 5), https://northwind-drift-agent.vercel.app

Median of 3 runs, headless Chromium from Bengaluru (about 170 ms round trip to Vercel bom1). "First visit" is a
fresh browser with an empty cache; "reload" is the same page reloaded. Time until the tab's main content shows.

| Tab | First visit before | First visit after | Reload before | Reload after |
|---|---|---|---|---|
| Overview (new default) | (did not exist) | 700 ms | (did not exist) | 150 ms |
| Change review (CHG-HEADLINE verdict) | 975 ms | 673 ms | 1,033 ms | 358 ms |
| Findings | 903 ms | 883 ms | 946 ms | 425 ms |
| Sources | 870 ms | 700 ms | 530 ms | 267 ms |
| Incident | 707 ms | 893 ms | 434 ms | 267 ms |
| Chat | 764 ms | 731 ms | 437 ms | 214 ms |
| Scale | 865 ms | 906 ms | 527 ms | 355 ms |

Targets: reload under 1 s (met on every tab; before, change review missed it at 1,033 ms); cold first visit
under 4 s (every first visit is under 1 s). These runs hit a warm function: a true cold start (new function
instance) was not isolated in this measurement; the API logs one `cold start:` line per instance with import
and first-request times. Local import time in public mode: 295 ms (LangGraph deferred, was about 1 s).
