# Config Rationale

Every setting in the Fuel config exists for a reason. This document explains the reasoning so you can adapt intelligently if the user has specific needs.

---

## The Two Biggest Money Wasters

Before diving into individual settings, understand what actually costs money with autonomous agents:

1. **Context accumulation** — Without pruning, every message stays in the context window. A 4-hour session can accumulate 200k+ input tokens per call. Context pruning and compaction fix this.
2. **Session initialization** — Loading full history, memory files, and prior messages on every session start burns 30-50k tokens before the agent does anything useful.

Model pricing matters, but it's a multiplier on top of these two factors. Fix the context problem first.

---

## Multi-Provider Arbitrage Strategy

Fuel routes each role to the cheapest available provider instead of sending everything through a single backend. This alone saves 35-75% on inference costs.

### Why multi-provider?

Different providers specialize in different models. No single provider is cheapest for everything. By evaluating cost, latency, and reliability per model, we route each semantic role to its optimal provider. Typical savings are 35-75% vs using a single provider for everything.

### Provider selection principles

- **Model creator APIs** — When a model creator offers a direct API, it's usually cheapest (no middleman markup) and fastest for their own model.
- **Specialized hardware providers** — Purpose-built inference hardware (e.g. custom ASICs) can be dramatically cheaper and faster for small models.
- **Multi-host fallbacks** — Each role has a primary provider plus fallback providers. Higher cost but high reliability. Fallbacks only activate when primaries are unavailable.
- **Continuous evaluation** — We regularly benchmark new models and providers. When a better option appears for a role, we roll it out gradually via the proxy's `FUEL_MODEL_MAP`.

### How routing works

```
Agent selects: fuel/worker
                ↓
OpenClaw strips provider prefix "fuel/" → sends "worker" to baseUrl
                ↓
Fuel Proxy rewrites "worker" → real provider/model ID
                ↓
Forwards to Bifrost gateway
                ↓
Bifrost routes to the configured provider API
                ↓
Response flows back through Fuel Proxy, which rewrites
the real model ID back to "worker" in the response
```

The proxy layer means instance configs use semantic role names (`worker`, `reasoning`, `heartbeat`) instead of real provider/model IDs. Model swaps happen at the proxy's `FUEL_MODEL_MAP` env var — invisible to the agent. This enables:

- **A/B testing** — Route a percentage of traffic to a new model by adding weighted mappings
- **Gradual rollout** — Shift traffic from old to new model incrementally
- **Instant rollback** — Revert to the previous model by changing one env var
- **Zero downtime** — Agent configs never change, no instance restarts needed

No changes needed to OpenClaw upstream — `parseModelRef()` in `model-selection.ts` handles the prefix natively.

---

## Model Selection

Instances use semantic role names — the fuel-proxy maps them to actual providers. Current mappings are always visible at [openclaw.rocks/fuel](https://openclaw.rocks/fuel).

### Role: Worker (`fuel/worker`)

- Primary model for all routine work: file edits, searches, code generation
- Optimized for: low cost per token, fast inference (sub-second first token), good coding quality
- Selection criteria: best cost/quality ratio for routine tasks, ideally from a model creator's direct API (lowest cost)

### Role: Reasoning (`fuel/reasoning`)

- Used for complex tasks and as fallback for worker
- Optimized for: highest SWE-Bench score, large context window, native tool-use training
- Selection criteria: best first-pass quality (fewer retries = fewer total tokens), agent swarm capability
- Per-token cost is higher than worker, but total session cost is often lower for complex tasks because of fewer retry loops

### Role: Heartbeat (`fuel/heartbeat`)

- Heartbeats are simple status checks — "is the agent alive, what's it doing"
- Optimized for: lowest possible cost, fastest TTFT (sub-100ms ideal)
- Selection criteria: smallest model that handles the task, cheapest provider with custom inference hardware
- 24 heartbeats/day at near-zero cost — effectively free
- Pure waste to use a reasoning model for this

### Why not a dedicated reasoning model (R1, etc.) by default?

- Reasoning models are 3-10x more expensive per token
- The current reasoning role model already scores high on SWE-Bench — sufficient for autonomous agent work
- The compaction and memory settings compensate for any edge cases
- If a specific task needs deep reasoning, the user can add a dedicated reasoning model as a named agent

---

## Context Pruning: `cache-ttl` with 6h TTL

**This is the single most impactful cost-saving setting.**

Without context pruning, token usage escalates continuously. A 4-hour session can balloon to 200k+ input tokens per call because every past message stays in context.

- `mode: cache-ttl` — maintains prompt cache validity for the TTL duration, then prunes expired messages
- `ttl: 6h` — 6-hour cache lifespan. Long enough to maintain context for a work session, short enough to shed stale messages.
- `keepLastAssistants: 3` — always preserves the last 3 assistant messages for continuity, even after pruning

**When to change:**
- Short tasks (< 1 hour): reduce TTL to `2h`
- Multi-day projects where old context matters: increase to `12h` (but costs more)
- If the agent seems to lose track of recent work after pruning: increase `keepLastAssistants` to 5

---

## Session Initialization

**Second biggest cost saver after context pruning.**

Without explicit rules, agents load full history, all memory files, and prior messages on every session start — 30-50k tokens before doing anything useful. The session init rules cut this to ~8k:

1. Load only: SOUL.md, USER.md, IDENTITY.md, today's memory file
2. Never auto-load: full MEMORY.md, session history, prior messages, previous tool outputs
3. Use `memory_search()` on demand for prior context — pull snippets, not entire files
4. Write daily summaries at session end

This is a system prompt addition, not a config setting — the agent must follow the rules itself.

**When to change:**
- If the agent seems to miss important prior context: add specific files to the auto-load list
- If sessions are very short (< 30 min): session init overhead matters less, can be relaxed

---

## Prompt Caching

**90% discount on reused content.**

System prompts, tool definitions, and stable preamble content get cached for 5 minutes. When the agent makes multiple calls within the TTL, cached content costs 90% less.

- `enabled: true` — turns on prompt caching
- `ttl: 5m` — cache lifespan. 5 minutes covers typical multi-turn exchanges.
- `priority: high` — prioritizes cache hits for system-level content

Combined with session initialization (small, stable prompts), this makes the per-call overhead near-zero.

**When to change:**
- Long-running sessions with stable system prompts: increase to `10m`
- Rapidly changing system prompts: caching provides little benefit, can be disabled

---

## Compaction: 40k threshold + Memory Flush

### `softThresholdTokens: 40000`

This is the point at which compaction triggers. At 40k tokens in the context window, the agent distills important information into a memory file and clears the window.

**Why 40k:**
- Tested in production across weeks of autonomous operation
- Below 30k: compacts too aggressively, agent loses useful context
- Above 50k: context grows too large before flush, wasting tokens on repeated large inputs
- 40k is the sweet spot where the agent has enough context to do good work but flushes before costs escalate
- The reasoning model's large context window gives plenty of headroom, but we compact early to save money

### `reserveTokensFloor: 20000`

Ensures the agent always has 20k tokens of room for a response after compaction. Prevents "context full, agent can't respond" failures.

### Memory Flush Prompt

The custom flush prompt is critical — it tells the agent **what to save** during compaction:

```
Extract key decisions, state changes, lessons, and blockers to memory/YYYY-MM-DD.md.
Format: ## [HH:MM] Topic. Skip routine work. Output NO_FLUSH if nothing important happened.
```

The `NO_FLUSH` escape valve prevents unnecessary file writes when the agent was just doing routine work. This keeps the memory directory clean and searchable.

### System Prompt

```
Compacting session context. Extract only what is worth remembering. No fluff, no routine operations.
```

Short and directive. The system prompt for compaction should not be long — it fires frequently and the tokens add up.

**When to change:**
- If the agent seems to forget important decisions: add specific categories to the flush prompt (e.g., "Include API endpoints discovered, error patterns identified")
- If memory files are too noisy: add exclusions ("Skip file reads, directory listings, routine git operations")

---

## Concurrency: 4 agents / 8 subagents

### Why lower than the default?

The default OpenClaw config allows 8 agents / 16 subagents. Fuel reduces this to 4/8 because:

- Higher concurrency = more parallel API calls = faster budget drain
- When a task gets stuck, retry loops can spawn multiple concurrent attempts
- 4/8 is enough parallelism for complex tasks while capping worst-case burn rate
- The bottleneck is usually the LLM response time, not concurrency

**When to change:**
- Heavy parallel workloads (many independent file edits): increase to `maxConcurrent: 8`
- Solo agent doing sequential tasks: drop to `maxConcurrent: 2`
- Never exceed 16 agents — diminishing returns and rate limit risk

---

## Heartbeat: 1 hour

**Why 1h:**
- Keeps long-running agents from being garbage-collected by the runtime
- Each heartbeat on the dedicated heartbeat role costs near-zero — effectively free
- 1h interval is infrequent enough to add zero meaningful cost (24 calls/day)
- More frequent heartbeats (30m) double the calls with no practical benefit for most workloads

**When to change:**
- Short tasks (< 1 hour): disable heartbeat entirely
- If the runtime garbage-collects at 30m: reduce to `30m`
- Multi-day autonomous runs: 1h is ideal

---

## Memory Search: local

**Why local:**
- Zero external dependencies — works offline, in air-gapped environments, anywhere
- Fast retrieval (milliseconds vs network round-trip)
- Searches both `memory/` files and past sessions
- Good enough for most workloads

**When to change:**
- If the agent needs semantic search across thousands of memory files, consider switching to an embedding-based provider
- For single-session work, local is always the right choice

---

## Tools: full profile

**Why full:**
- Autonomous agents need every tool available: file operations, search, shell, browser, etc.
- Restricting tools forces the agent to work around limitations, which costs more tokens
- The `full` profile includes all built-in tools

**When to change:**
- Security-sensitive environments: use `coding` profile (no shell/browser)
- Read-only monitoring agents: use `minimal` profile

---

## Multi-Agent Cost Strategy

If running multiple agents (coordinator/worker pattern), the cheapest architecture is:

| Agent Role | Fuel Model | Why |
|---|---|---|
| Coordinator | `fuel/reasoning` | Needs reasoning for task decomposition and strategic decisions |
| Worker (coding) | `fuel/worker` | Routine code generation — worker quality is sufficient and much cheaper |
| Worker (monitoring) | `fuel/heartbeat` | Read-only status checks — cheapest and fastest |
| Worker (research) | `fuel/reasoning` | Research needs reasoning model's tool orchestration |
| Heartbeat | `fuel/heartbeat` | Simple alive check — near-zero cost |

**Key principle from production:** "Expensive models stay out of the hot path." Only coordinator and research agents need `fuel/reasoning`. Workers, monitors, and heartbeats use the cheapest model that handles the task.

---

## Cost Model

The cost values in the config are approximate per million tokens in USD. Each role routes to its cheapest provider. We update these as we swap models and providers — current values are always visible at [openclaw.rocks/fuel](https://openclaw.rocks/fuel).

| Role | Approx. Input | Approx. Output | Use case |
|---|---|---|---|
| `fuel/worker` | ~$0.28 | ~$0.42 | Routine coding, subagents |
| `fuel/reasoning` | ~$0.50 | ~$2.80 | Complex reasoning, research |
| `fuel/heartbeat` | ~$0.05 | ~$0.08 | Heartbeats, status checks |

For reference, a typical autonomous coding session (1 hour, moderate complexity) with Fuel config:
- ~300K input tokens (down from ~500K without context pruning)
- ~100K output tokens (mix of worker + reasoning)
- Blended Fuel cost: ~$0.45 (down from ~$0.80 with a single provider)
- Same session without context pruning/compaction/session init: ~$1.80

The savings come from two layers:
1. **Multi-provider arbitrage** (35-75% on inference) — routing each role to the cheapest provider
2. **Context management** (55% on token volume) — pruning, compaction, session init, prompt caching

---

## Region Filtering

### Non-political default

**"Best models. No borders."** — We pick the best model for each role purely on quality, cost, and reliability. Where it comes from doesn't factor into our default selection. This is a technical decision, not a political one.

But we respect that users have legitimate reasons to filter: data sovereignty regulations, compliance requirements, corporate policy, or personal preference. Region filtering makes this easy without compromising the default experience.

### Two dimensions

Region filtering operates on two independent axes:

1. **Model origin** — Where the model company is headquartered. A model with origin `cn` means the company that trained it is based in China. This matters if your policy restricts which companies' models you can use.

2. **Provider region** — Where the API endpoint is physically hosted. A provider region of `us` means the inference runs on US-based infrastructure. This matters for data sovereignty — your prompts and responses only transit through servers in the specified region(s).

These are independent. DeepSeek V3 has origin `cn` but can be hosted by Fireworks in the US (provider region `us`). The same model, different hosting.

### How it works

The filter is encoded as a `~` path segment in the baseUrl:

```
/v1/chat/completions              → all/all (default, no filter)
/v1/~us-us/chat/completions       → origin=us, provider=us
/v1/~all-us/chat/completions      → any origin, US providers only
/v1/~us,eu-us,eu/chat/completions → US+EU origin, US+EU providers
```

Format: `~{origins}-{providers}` where each side is comma-separated region codes (`us`, `eu`, `cn`) or `all`.

The proxy strips the filter segment before forwarding to Bifrost. Candidates that don't match the filter are excluded, and weights are re-normalized among remaining candidates. If no candidates remain, the proxy returns a 400 with available regions listed.

### Impact on available models

Filtering reduces your options but every filter has full role coverage. Current region availability:

| Role | Model | Origin | Provider Region |
|------|-------|--------|----------------|
| worker | DeepSeek V3 (DeepSeek) | cn | cn |
| worker | DeepSeek V3 (Fireworks) | cn | us |
| worker | Devstral 2 123B (Mistral) | eu | eu |
| worker | Llama 4 Maverick (Together AI) | us | us |
| reasoning | Kimi K2.5 (Together AI) | cn | us |
| reasoning | Magistral Medium (Mistral) | eu | eu |
| reasoning | gpt-oss-120b (Together AI) | us | us |
| heartbeat | Llama 3.1 8B (Groq) | us | us |
| heartbeat | Mistral Small 3.2 (OVHcloud) | eu | eu |

**Every region filter has full role coverage:**

| Filter | Worker | Reasoning | Heartbeat |
|--------|--------|-----------|-----------|
| `~all-all` (default) | DeepSeek V3 @ DeepSeek | Kimi K2.5 @ Together AI | Llama 8B @ Groq |
| `~all-us` | DeepSeek V3 @ Fireworks | Kimi K2.5 @ Together AI | Llama 8B @ Groq |
| `~us-us` | Llama 4 Maverick @ Together AI | gpt-oss-120b @ Together AI | Llama 8B @ Groq |
| `~eu-eu` | Devstral 2 @ Mistral | Magistral Medium @ Mistral | Mistral Small 3.2 @ OVHcloud |

**Cost impact:** Fallback providers may have different pricing than the primary. US-hosted alternatives are typically 20-50% more expensive than direct API access from the model creator. EU-sovereign alternatives may also carry a premium. The proxy handles this transparently — weight 0 fallbacks only activate when the region filter excludes the primary.

### GDPR compliance

Use `~eu-eu` for fully GDPR-compliant inference:

- **EU-sovereign providers only**: Mistral (Paris, France) and OVHcloud (Roubaix, France) — both EU-headquartered
- **Signed DPAs**: All EU providers offer Data Processing Agreements
- **EU data residency**: Prompts and responses never leave the EEA
- **No CLOUD Act**: No US-headquartered companies in the data path
- **Stateless proxy**: We don't log, store, or retain prompts or responses. Budget tracking uses only metadata (token counts, costs) — never content.

The EU stack uses exclusively French companies with strong data sovereignty guarantees. OVHcloud additionally holds **HDS certification** (Hébergeur de Données de Santé) for healthcare data hosting.
