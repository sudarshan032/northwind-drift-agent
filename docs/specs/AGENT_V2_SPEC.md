# AGENT_V2_SPEC: make a real agent the centre of the project

Why: the hackathon brief is "an agent that acts on connected data, remembers what it learns, and can show how it got to each answer", and Track 1 focuses on multi-hop reasoning, graph queries and algorithms as agent tools, tool selection through MCP, actions based on what the graph shows, and explainable results. Today the agent is a thin layer over deterministic code. This spec makes the agent the main actor, while detection stays deterministic.

Deadline: everything done, deployed and documented by **Oct 17, 20:00 IST**. Cut order if late is in section 9. Work on branch `agent-v2` from master; follow CLAUDE.md. Keep all tests passing.

## 1. Models and cost guard

- Provider: OpenAI. Default model `gpt-6-luna` for the tool loop (research the current OpenAI docs: model ID, tool calling, structured output, and whether the Responses API or Chat Completions is the right interface; cite in RESEARCH_NOTES.md). Optional escalation model for the final verdict only (`ESCALATION_MODEL`, e.g. a stronger OpenAI model or Anthropic Claude Haiku 5.5 via `claude-haiku-5-5`), used only if the evaluation in section 7 shows Luna gets verdicts wrong.
- Keys from `.env` / Vercel env only (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`); never printed or logged. Keep OpenRouter and the fake LLM as options; tests always use the fake LLM.
- Cost guard: count tokens per call and convert to dollars with the documented prices; a daily dollar cap (`AGENT_DAILY_USD`, default 1.00) and per-visitor cap on the public site (hashed IP, default 5 investigations per day). Show "today's agent spend" in the header. When a cap is hit, fall back to cached runs with a plain message.

## 2. The Change Guardian agent (the main actor)

Trigger: a change request is submitted or selected (UI button "Ask the agent to review"), or a symptom is entered in Incident. The agent runs a tool loop: it decides which tool to call next from what it has found so far, up to 10 steps, with the last step forced to produce a structured verdict (the existing no-tools final step).

Expected behaviour for a change review (the agent chooses the order; this is guidance in its prompt, not code):
1. compute declared and real radius of the target;
2. if they differ, fetch the hidden links and the evidence source for each;
3. for newly reached components, find observed vs declared owners;
4. if any impacted component holds patient data, check its protection chain;
5. recall memory about the target and impacted components (section 4);
6. check recent changes and incidents on the shared components;
7. decide: risk level, the reasons, who to warn, which actions to take.

The structured verdict: `{risk, reasons[], hidden_links[], warn[], actions[], evidence[]}`, validated by the existing grounding checks (numbers, CI names, and the most-specific-shared-dependency rule for incident causes). Invalid verdicts are rejected and replaced by the rule-based summary, with the rejection shown in the trace.

## 3. Tools through MCP, and actions

- Expose the read-only graph tools as an **MCP server** (streamable HTTP at `/mcp`, plus a stdio entry point for local clients): radius (declared/real), hidden links, impact path (shortest path), who to page, protection check, access check, shared dependencies, changes on, critical nodes (betweenness), component lookup, neighbourhood, search, recall memory. Each tool has a clear description so the model can choose well.
- The agent **selects tools through MCP** (research whether OpenAI's API can call a remote MCP server directly, or use an MCP client in the app; pick the approach that works on Vercel and log the decision).
- **Action tools** are separate, not on the public MCP server, and all write to the graph: `add_change_note` (a ServiceNow-style work note on the change), `request_cab_review`, `notify_owner` (mock notification with reason), `propose_cmdb_fix` (creates the existing approval-gated finding), `open_ticket` (existing ticket shape). Actions that change the CMDB still need human approval; notes, CAB flags, notifications and tickets are recorded immediately and shown in the UI.
- README: how to connect Claude Desktop or any MCP client to the read-only MCP server (local and live URL).

## 4. Memory in FalkorDB (remembers across sessions)

- A separate graph per tenant using FalkorDB's multigraph support: `northwind_memory` (public demo) plus `northwind_memory_<user>` when a user id is supplied, showing per-user isolation.
- Nodes: `Investigation` (trigger, time, model, cost, verdict), `Step` (tool, arguments, result summary, reason), `Decision` (risk, actions), `Fact` (a learned statement about components, with status `confirmed` / `rejected` / `proposed`, source and time), `Feedback` (a human's approve/reject, with comment). Facts and investigations link to components by name (cross-graph reference).
- Learning loop: human approval of a finding creates a confirmed Fact; rejection creates a rejected Fact. The `recall` tool returns prior investigations, confirmed facts and rejected facts for given components. Triage and the agent must not re-raise a finding a human rejected (unless new evidence appears) and must mention relevant confirmed facts.
- Demo: after approving the headline fix and resetting the graph data, a new review of a change on `dc1-host-07` shows the agent recalling "a person confirmed 51 hidden links here on <date>". "Reset demo" clears memory too, with a separate "reset memory" option.

## 5. Visible reasoning (trace)

- In Change review and Incident, an "Agent investigation" panel that streams each step as it happens: step number, tool chosen, why (one sentence from the model), a short result, and time/cost. Then the verdict and the actions taken, each linked to the components involved.
- Past investigations are listed (from memory), each re-openable with its full trace.
- Cached demo runs are clearly labelled "recorded run".

## 6. More scenarios (so the agent has real work)

Add 4 incident scenarios to the drift data, each with a symptom text and a known root cause, using the existing drift types: a failing backup on a patient-data system, a wrong owner causing slow response, the unregistered SMART on FHIR app (security), and a stale dependency misleading a change review. Record expected answers in `ground_truth.json` under `scenarios`. Do not change existing drift counts or tests; add new tests.

## 7. Evaluation (proof the agent works)

- `python -m agent.eval` runs the agent live on the 5 scenarios (headline plus the 4 new ones) and scores: correct root cause, correct risk, correct owners warned, grounding passed, steps used, cost. Run once with `gpt-6-luna` (and with the escalation model only if Luna scores below 4/5). Report results in `docs/records/AGENT_EVAL.md` and a short table in the README.
- Tests with the fake LLM cover: tool loop and step cap; MCP tool listing and calls; each action; memory write and recall; rejected-fact suppression; cost caps; trace streaming.

## 8. Docs, site and deploy

- Overview: the four steps become "the agent investigates, decides and acts", with the trace screenshot; mention MCP and memory.
- README: architecture (agent loop, MCP tools, actions, memory graph), the eval table, cost per investigation, how to plug in an MCP client.
- Cache recorded runs for the demo flows. Deploy: preview first, check (one live investigation, MCP endpoint lists tools, memory recall after reset), then production.
- Tracks to name on submission: Track 1 (primary), Track 2 (memory and shared state), Track 3 (who owns what, decisions traced to sources).

## 9. Cut order if late

Cut first: per-user memory graphs (keep one memory graph), stdio MCP entry, past-investigation list, escalation model. Never cut: the agent tool loop with visible trace, MCP tools, actions, memory recall in the demo, the evaluation table.