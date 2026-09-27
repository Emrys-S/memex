# Model council for the second brain: integrated design (v2)

**Status, 2026-09-19.** This supersedes two documents and is the one to build against: the original design written 2026-08-24 in the claude.ai Project (archived verbatim as [`librechat-model-council-design-original-2026-08-24.md`](librechat-model-council-design-original-2026-08-24.md)), and a same-day rebuilt draft I wrote before the original was available (folded into this and removed). Both were reviewed against each other, against primary sources, and against the live LibreChat deployment on schoerro. Section 2 records what each contributed, what was corrected, and where they disagreed and how that was resolved. Section 7 solves the problem both documents flag but neither resolves: peer review by LLMs is biased.

Model IDs and prices are an OpenRouter snapshot from 2026-09-19 and will drift; the build reads them from a roster file.

---

## 1. Decision in brief

- **Build the council as its own small MCP service, `council-mcp`,** which calls `second-brain-mcp` for retrieval and is attached to LibreChat like any other MCP server. LibreChat stays the front end; the council doesn't depend on it, so it also works from Claude Code, Goose, and claude.ai/Cowork if the remote connector ever gets built.
- **Design: an evidence-grounded, blind council.** Independent parallel answers from models of different lineage, grounded in numbered evidence from your own vault and Zotero library; a **grounded review** in place of holistic peer ranking; a chair that reports agreement, disagreement and gaps rather than a single blended answer.
- **Route by clearance, not by model.** What matters for privacy is where a prompt is processed, not whose weights are running. Open-weight Chinese-lab models can be served by Western hosts with zero data retention, which lets the council keep its lineage diversity without sending private notes to Chinese-hosted infrastructure.
- **Trial before building.** Phase 1 is a manual council using what LibreChat already has, run as an A/B on your own questions, because the evidence that councils beat one strong model on open-ended research is thin.

## 2. What each design contributed

| | Original (2026-08-24) | Rebuilt draft (2026-09-19) | Integrated design |
|---|---|---|---|
| Pattern and lineage | Karpathy 3-stage; Perplexity's version | Same, plus what the research literature says | Both; see section 4 |
| Foundation | LibreChat vs Big-AGI vs Onyx, with sourced findings | Council as a tool, so the front end is a free choice | Kept the comparison; made the choice matter less |
| Trial | Five member agents plus a chairman in the Agent Builder, pasting answers by hand | LibreChat side-by-side (`multiConvo`), pasted evidence pack, blind scoring | Side-by-side with a shared pack as the base trial; the per-member-agent variant kept as the A/B arm (section 9) |
| Service shape | Separate `council-mcp`, timed against the VPS decision | Tool inside `second-brain-mcp` | **Separate `council-mcp`** (original), on the NAS (my draft's cost analysis: it is network-bound), without torch |
| Retrieval | **Each member searches for itself**: five retrieval paths are part of the diversity signal | **One shared evidence pack**, so claims can be checked and only retrieved passages leave the NAS | **Both, staged**: shared pack first; bounded per-member search added through a clearance-enforcing proxy (section 5) |
| Roster | Diversity of lineage over headcount: Claude, Gemini, DeepSeek, Kimi, GLM | Western frontier seats, Chinese seats only in an "open" tier | Original's roster as the default, with **clearance routing** deciding where each seat may run |
| Privacy | Flagged the jurisdiction question; suggested a routing rule "later" | Tiers enforced in code, ZDR on every call | Enforced in code; refined by the host insight above |
| Peer review | Keep Karpathy's ranking at `full` depth; aware of self-preference and verbosity bias | Replace ranking with structured critique | **Grounded review** (section 7): claim-level verification with checkable receipts; ranking demoted to an optional, bias-audited signal |
| Depth dial | `quick` / `full` | `quick` / `full` | `quick` / `full` / `deep` |
| Chair output | Consensus, divergence, carried citations | Agreement, disagreement, unsupported claims, blind spots | Union of both, with citations checked by code |
| Cost | "Roughly six times a single query" | Priced from live rates, with caps | Live-priced, three depths, caps (section 8) |
| Evidence base | Weaknesses argued from general knowledge | Sourced (Self-MoA, MAD, Artificial Hivemind, judge biases) | Sourced (section 3) |
| Evaluation | Trial "within a few real questions" | Blind scoring and a decision rule | Blind scoring, plus a bias-calibration harness (section 7.4) |
| Web search | Later extension, separate jurisdiction question | Keenable already wired into LibreChat | Later extension, unchanged |

**Corrections to the original** (checked 2026-09-19; the original was correct when written except where noted):

1. **LibreChat does have side-by-side.** #3659 is *closed* and is about keeping multi-response generations when you switch chats, not a missing feature. The `multiConvo` option (already on) "enables … multiple response streaming", i.e. streaming from more than one model at once. I haven't verified how many models it fans out to; the docs' wording says two.
2. **LibreChat is no longer Anthropic-only.** An OpenRouter custom endpoint is configured (`fetch: true`), and the code shows custom endpoints appear regardless of the `ENDPOINTS` env var. There is no need to add a `Council (OpenRouter)` endpoint.
3. **Model slugs have moved on:** `google/gemini-3-pro-preview` is gone (now `gemini-3.1-pro-preview`); `z-ai/glm-5.3` succeeds 5.2; `qwen/qwen3.8-max` is `qwen3.8-max-0902` and has no zero-retention endpoint; MiniMax M3 exists; Claude Sonnet 4.5 is superseded by the Claude 5 family (Opus 5, Sonnet 5).
4. **Agents can't be created from a file.** The five-agents-plus-chairman plan is hand-built UI work stored in MongoDB, which is fine for a trial and wrong for anything you want reproducible.
5. **The jurisdiction concern is narrower than stated.** "Processed on that lab's inference infrastructure, subject to Chinese data-handling rules" holds only if the request is routed to the lab's own endpoint. OpenRouter lists 9 zero-retention hosts for DeepSeek V4 Pro, 15 for Kimi K3 and 23 for GLM-5.3, mostly independent inference companies, so provider pinning can keep the lineage and change the jurisdiction.
6. **NAS load is less of a problem than feared.** The fan-out is HTTP waiting, not compute. The only CPU-heavy step, embedding search, already runs in `second-brain-mcp`. `council-mcp` needs no torch and should fit in a few hundred MB, so it doesn't have to wait for the VPS decision.
7. **Big-AGI #892 (MCP support) is still open** as of 2026-09-19: no branches, PRs, maintainer replies or other activity. Onyx's Lite-mode-plus-MCP question and #574 were not re-checked.

## 3. Evidence and honest limits

| Design | What the literature says |
|---|---|
| **Karpathy council** ([repo](https://github.com/karpathy/llm-council)) | Independent answers, anonymised peer ranking, chair synthesis; failed members are dropped rather than fatal. Popular and workable; not itself evidence of better answers. |
| **Perplexity Model Council** ([announcement](https://www.perplexity.ai/hub/blog/introducing-model-council)) | Parallel members, chair reports agreement and disagreement, no ranking step. |
| **Mixture-of-Agents** ([arXiv 2406.04692](https://arxiv.org/abs/2406.04692)) | Layered proposers and aggregators; the basis of LibreChat's `chain`. |
| **Self-MoA** ([arXiv 2502.00674](https://arxiv.org/abs/2502.00674)) | Sampling one top model repeatedly often beats mixing different models, because mixing lowers average quality. **Keep every member near frontier quality.** |
| **Multi-agent debate** ([2311.17371](https://arxiv.org/abs/2311.17371), [2502.08788](https://arxiv.org/abs/2502.08788)) | Often fails to beat chain-of-thought or self-consistency at higher cost, is tuning-sensitive, and improves with **heterogeneous models**. No multi-round debate here. |
| **Artificial Hivemind** ([arXiv 2510.22954](https://arxiv.org/abs/2510.22954)) | Different model families converge on strikingly similar open-ended answers; more models is not more perspectives. |
| **LLM-judge biases** ([2410.21819](https://arxiv.org/abs/2410.21819), [survey 2412.05579](https://arxiv.org/abs/2412.05579)) | Self-preference, position and verbosity bias. |

Limits worth keeping in view:

- The benchmark research is on tasks with right answers. **For open-ended research judgement there is little direct evidence that a council beats one strong model plus good retrieval.** The trial in section 9 exists to find out.
- A council is for **interpretation and framing** (multistakeholder vs multilateral governance, competing definitions of digital sovereignty, Global-Majority framings), not fact retrieval and not drafting. The original's point stands: because every member is grounded in the same corpus, a disagreement more likely reflects a real interpretive split in the sources than five different hallucinations.
- **Agreement is weaker evidence than it looks** (the hivemind result), which is why the design treats *disagreement and gaps* as the product and flags unsupported convergence.
- **Cost habit.** A council can quietly become the most expensive thing in the stack. It is a deliberate tool, not a default.
- **Voice.** Blending models pulls prose to the middle. Use the council upstream (what to argue) and the `emrys-voice` spec downstream (how to say it).

## 4. Foundation: LibreChat, Big-AGI or Onyx

The original's comparison stands, updated where re-checked:

- **Big-AGI** has a native council-like feature (Beam: parallel fan-out, Fuse/Guide/Score merging, a Council mode in development) but no MCP client and no general custom-tool mechanism. #892 (opened 2025-11-28) is still open with no activity, so it cannot reach `second-brain-mcp`. Useful at most as a synthesis engine for questions with pasted context.
- **Onyx** has a solid MIT licence (use the `onyx-foss` mirror) and an HTTP-only MCP client, and it's the model for this project's citation discipline. But its Standard mode needs 4+ vCPU and 10+ GB RAM, more than the NAS has and heavier than the whole-stack VPS budget, and its Lite mode disables indexing. Whether Lite mode can still run MCP Actions is unverified.
- **LibreChat** is deployed, has `second-brain-mcp` wired in and runs on the NAS. Its native council-relevant features: `chain` (sequential mixture-of-agents, beta, max 10; later agents see earlier outputs, so there is no independent first round and no blinding), `subagents` (isolated child runs; parallelism undocumented; a `modelSpecs` entry can only reference existing Agent ids), and `multiConvo` (side-by-side streaming).

Because the council is an MCP service, **the front-end choice stops being load-bearing for this feature**, which addresses the original's question about widening deferred decision #3 to "LibreChat vs alternatives": that decision can be made on the other merits.

## 5. Architecture

### 5.1 Components

- **`council-mcp`** (new container on the NAS): Python slim image with `httpx` and `mcp`, no torch. Its own SOPS-encrypted `.env.enc` holds `OPENROUTER_API_KEY` and the Anthropic key (Claude goes direct to keep prompt caching, as the original insisted). Deployed with `sops-deploy.sh` like every other service. Run logs go to `council-mcp/runs/<timestamp>.json`.
- **A new structured tool on `second-brain-mcp`,** `search_evidence(query, sources, top_k)`, returning JSON items `{source, title, link, score, text}`. Today's `query_second_brain` returns formatted text; the council needs source tags and raw passages to enforce clearance and to verify quotes. This is a small patch to the canonical `mcp_server.py`, and it keeps retrieval logic in exactly one place.
- **LibreChat:** `council-mcp` added to `mcpServers` with a longer timeout (about 600 s), and the Second Brain spec's `promptPrefix` extended to say when to offer the council.

### 5.2 Tool

`run_council(question, depth="quick|full|deep", models=None, chair=None, private=False, sources="", zotero_collection="", outline="")`

`quick`: 3 members, no review, chair. `full`: full roster, grounded review, chair. `deep`: `full` plus a counterargument pass and an audited ranking (section 7). An `outline` argument switches to outline mode: each member critiques the outline section by section against retrieved evidence, and the outline is treated as private (unpublished writing) by default.

### 5.3 Flow

**Stage 0. Evidence.** Retrieve a **common pack** once via `search_evidence`, numbered `[E1]…[En]`. **Stage 1. Blind independent answers** in parallel. Each member gets the question, the pack, and a light lens (strongest case; skeptic; implementer; power-and-dependency), rotated per run and logged so a lens is never confounded with a model. Prompt rules: ground claims in `[E#]`; mark anything from your own knowledge `[unsourced]`; never cite outside the pack; say what evidence is missing; state what would change your mind. **Stage 2. Grounded review** (section 7). **Stage 3. Chair report** (5.6).

**Per-member search (v2).** The original's strongest idea, and one my draft undervalued: members deciding for themselves what to retrieve makes retrieval variance part of the diversity signal. It is added in v2 as a bounded loop (at most 3 calls) through a **proxy inside `council-mcp`** that (a) filters results to the member's clearance, (b) adds every returned item to the run's **union pack** with a stable ID, so verification and the chair can check any claim against anything any member saw, and (c) logs who retrieved what. Members have exactly one tool, read-only search; nothing else. Whether it is worth the extra tokens and code is settled empirically in phase 1.

### 5.4 Clearance routing

Two properties, kept separate:

- **Item sensitivity.** *Public:* `zotero`, `bluesky`, `linkedin` (published literature and public posts, following the original's judgement). *Private:* `boox`, `personal`, `people`, `meeting-notes`, `inbox`, `projects`, `nas-ops`, `other`, and anything unrecognised. The question is public unless `private=true`; an outline is private.
- **Route class** (where the prompt is processed):
  - **R1:** first-party Western labs with zero retention: Anthropic direct, OpenAI via Azure, Google, Mistral (EU).
  - **R2:** Western third-party hosts serving open-weight models with zero retention, from an allow-list you approve (Fireworks, DeepInfra, Baseten, Crusoe and Parasail are candidates; check each host's jurisdiction and quantisation yourself).
  - **R3:** endpoints run by the Chinese labs themselves (Moonshot, Z.ai, DeepSeek, Alibaba).

**Rule, enforced in code:** private items only go to R1 (and R2 if you opt in); R3 is off by default. Public packs may use R1 and R2. Every OpenRouter call sends `provider: {zdr: true, data_collection: "deny", only: [allow-list]}` ([docs](https://openrouter.ai/docs/features/provider-routing)); switch on the account-wide equivalent as a backstop. If a private item is in the pack and R2 isn't approved, the council runs on R1 seats only.

### 5.5 Roster

| Seat | Model | Route | $/1M in/out | Notes |
|---|---|---|---|---|
| A, chair | `claude-opus-5` | R1, Anthropic direct | 5 / 25 | Prompt caching kept |
| B | `google/gemini-3.1-pro-preview` | R1 | 2 / 12 | Long context, as in the original |
| C | `deepseek/deepseek-v4-pro` | R2 | 0.42 / 0.84 | MIT; 9 ZDR hosts |
| D | `moonshotai/kimi-k3` | R2 | 1.7 / 8.5 | Custom licence, fine at this scale; 15 ZDR hosts |
| E | `z-ai/glm-5.3` (5.2 also live) | R2 | 0.91 / 2.86 | Agent-oriented; 23 ZDR hosts |
| Bench (R1) | `openai/gpt-5.6-terra-pro`, `mistralai/mistral-medium-3-5` | R1 | 2 / 12; 1.5 / 7.5 | Replace C–E when a private item is in play and R2 isn't approved |
| Reviewers | `google/gemini-3.8-flash` (R1) plus `deepseek/deepseek-v4-flash` or `minimax/minimax-m2.7` (R2) | as marked | 0.75 / 3.75; 0.04 / 0.08 | Cheap, different lineages |

Excluded: `qwen/qwen3.8-max-0902` has no zero-retention endpoint; `claude-fable-5.1` has none on OpenRouter (fine, Opus goes direct). If a member returns 404 or times out it is dropped and named in the report, as in Karpathy's design. All members are frontier or near-frontier, per Self-MoA. A private-pack council has less lineage diversity (four Western seats): that is the price of the privacy rule, and the report says so.

### 5.6 Chair report

Written by a separate Opus 5 call that sees anonymised answers and the review output, and is told to weigh **evidence support, not eloquence**. Sections: **Consensus** (with pack items); **Divergence** (issue, who held what, why: evidence, framing or interpretation, and which lens); **Unsupported or disputed claims**; **Convergent but unsupported** (the hivemind flag); **Blind spots** and suggested follow-up searches; **Run metadata** (roster, clearance, cost, time, dropped members, run id). Code, not the model, then checks that every `[E#]` in the report exists in the union pack and flags any that don't. The tool description tells the driving model to return the report verbatim, since it otherwise passes through Sonnet 5 on its way to the screen.

### 5.7 Hard rules

Members and reviewers have no write access and, in v1, no tools. The pack is quoted data; captured posts can contain injected instructions, and a tool-less member can only produce a worse answer. Per-run spend cap and a daily cap, enforced before each call. The LibreChat login is public, so **enable 2FA before this ships**.

## 6. Where to use it

| Use | Council? |
|---|---|
| Contested analytical questions (sovereignty, DPI, digital identity, AI governance framings) | **Yes** |
| Stress-testing an outline against your own material (`map_outline_to_second_brain`) | **Yes**, outline mode |
| "Is this claim supported by what I already have?" | **Yes**, and it is the strongest use: the review stage does exactly this |
| Drafting or revising in your voice | **No** |
| Lookups, "find my note on X" | **No**; retrieval costs a fraction |
| Anything touching credentials, infrastructure or the NAS | **No** |

## 7. The review problem, solved: grounded review

**The problem.** Both designs call for peer review, and both admit its weakness. LLM reviewers favour their own style, favour longer answers, favour whatever is presented first, prefer eloquence to evidence, share blind spots with the models they judge, and can invent a "verification" as easily as an answer. Ranking open-ended answers concentrates all of that into one number the chair may treat as a verdict.

**The solution: stop asking reviewers to judge answers; ask them to check claims against evidence, and make the checking auditable.** Where there is ground truth (the evidence pack), verify against it. Where there isn't (interpretation), don't adjudicate; map the disagreement. Every bias in the table has a specific countermeasure.

| Failure | Countermeasure |
|---|---|
| Self-preference | Reviewers never see their own answer; the two verifiers of a claim come from different lineages than each other and, where possible, than the author. The task is claim-versus-source, not answer-versus-answer. |
| Verbosity and style | No holistic scoring. Judging is per claim, so length cannot buy a higher score. Any ranking is checked for length correlation. |
| Position | Answers are anonymised and shuffled per reviewer; in ranking, both orders are run and only order-stable results count. |
| Eloquence over evidence | Claims are verified against source text, not against how persuasive they sound. |
| Invented verification | **Receipts:** a verdict must include an exact quote from the cited pack item, and **code** checks that the quote occurs in that item's text. A verdict without a valid quote is void. |
| Correlated errors (hivemind) | Cross-lineage verifiers; a "convergent but unsupported" flag when three or more members assert the same claim and the evidence pack doesn't back it. |
| Cost | The verifiers are cheap models doing a narrow task, which is both cheaper and less biased than frontier models grading each other. |
| Adjudicating contested interpretation | Not attempted. A stance matrix shows each member's position; the decision stays with you. |
| Chair smoothing away provenance | Chair citations are checked by code (5.6); every claim carries its pack IDs. |

**Steps.**

- **R0. Mechanical pass (code only).** Every `[E#]` a member cited exists; every quoted span appears verbatim in its cited item; uncited claims are marked `unsourced`.
- **R1. Claim extraction.** A cheap model returns each answer as JSON claims `{id, text, type: factual | interpretive | normative, cites}`.
- **R2. Verification with receipts (factual claims).** Two cheap verifiers from different lineages each return `supported | contradicted | not_in_pack` plus an exact quote. They see the cited items plus the three pack items with the highest keyword overlap (so an uncited-but-supported claim can still be found). Code validates the quotes. Disagreement between verifiers marks the claim `disputed`, with both quotes shown. `not_in_pack` is reported as "no support in your material", never as "false".
- **R3. Stance matrix (interpretive and normative claims).** Claims are clustered into issues; each cell is one member's position with its citations. No scores.
- **R4. Counterarguments (`deep` only).** Each member receives two other answers, anonymised, and lists the strongest counterargument or omitted pack evidence for each. The output is a list, not a rating.
- **R5. Audited ranking (`deep`, optional).** Pairwise comparisons, both orders, cross-lineage, self excluded. The run reports its own reliability: order-flip rate, length-to-rank correlation, inter-reviewer agreement. If any check fails, the ranking is shown labelled unreliable and **the chair does not use it**. The chair works from verified-claim tallies and the stance matrix. This keeps the original's wish that peer ranking remain available for contested framings while removing its power to decide.
- **R6. Convergence check.** As above.
- **R7. Report assembly by code.** Per-member tallies (supported, contradicted, unsupported, disputed), the stance matrix, the dispute list and flags, handed to the chair.

**7.4 Calibrate before trusting it.** Run once for each roster change, then quarterly. (a) *Planted errors:* take 20 real answers, insert one or two fabricated claims or citations into each, and measure each verifier's catch rate. (b) *Padding:* lengthen answers without adding content and see whether ranking shifts. (c) *Order swap:* rerun ranking with positions reversed and measure the flip rate. Replace any reviewer that catches under about 80% of planted errors or flips over about 20% of the time. The cost is a few dollars. This also gives you your own measured numbers instead of borrowed literature.

**What this doesn't solve, stated plainly.** Interpretive questions have no ground truth, so the council maps them rather than settling them. A verifier is only as good as the pack: absence of support is not falsity. Shared blind spots across all models remain; the lineage mix and the convergence flag reduce that but cannot remove it. The final judgement is yours, and the report is built to make that review quick: it lists what to check, ordered by consequence, with links to the pack items.

## 8. Cost and latency

Live-priced from the 2026-09-19 snapshot: 7k-token pack, about 1.5k tokens per answer, 2.5k chair output.

| Depth | Public-pack roster (5 seats) | Private-pack roster (4 R1 seats) | If members reason at length (×4 output) |
|---|---|---|---|
| `quick` (3 members, no review) | ≈ $0.23 | ≈ $0.26 | ≈ $0.6–0.7 |
| `full` (grounded review) | ≈ $0.35 | ≈ $0.35 | ≈ $0.8 |
| `deep` (adds counterarguments) | ≈ $0.53 | ≈ $0.53 | ≈ $1.1 |

Grounded review costs about $0.05–0.06 on top of Stage 1 and the chair, less than a frontier-model critique round would, because verification runs on cheap models. Latency is roughly 1–2 minutes (`quick`) to 3–6 minutes (`deep`) with no progress display in LibreChat. Per-run and daily caps are part of the build. Suggested: $1.50 per run, $10 per day.

## 9. Build plan and evaluation

**Phase 0. Diagnose retrieval (blocks everything).** On 2026-08-25 you noted LibreChat's second-brain results "haven't been that helpful", and that is still undiagnosed. Bring three to five concrete bad queries; run each through `query.py`; decide whether the fault is relevance, chunking, synthesis or the question. A council on top of poor retrieval only produces confident disagreement about poor evidence. The Zotero index is also still building its search graph (`zotero_library_v2`; the big segment is about 88% built but progress has crawled because torrent seeding is saturating the same disk), so Zotero queries currently fall back to slower brute-force scans, which may explain some earlier results.

**Phase 1. Manual trial (about an hour per round).** Ten real questions. For each, produce and blind-label: (A) the current single-model Second Brain answer; (B) a manual shared-pack council: run `query_second_brain` once, paste the same pack into three or four models via LibreChat side-by-side; (C) the original's variant: per-member agents, each with `second-brain-mcp` attached, so each retrieves for itself. Score each 1–5 on usefulness and "would I act on this", count unsupported claims found, and note material points that appear in one output and not the others. B against C settles the retrieval question (shared pack vs per-member search) empirically. Adopt the council only if it surfaces a material point the single run missed in at least 6 of 10 at a cost you would pay routinely.

**Phase 2. Build `council-mcp` v1.** Shared pack, blind answers, grounded review, chair report, `quick` and `full`; the `search_evidence` tool; roster file; clearance routing; caps; run logs; SOPS secrets; LibreChat wiring. Run the calibration harness (7.4) before relying on the review.

**Phase 3.** Per-member search through the proxy, if phase 1 favoured it; `deep` mode.

**Phase 4.** Outline mode; optional web-search tool (Keenable is already in LibreChat; a Perplexity-backed search would add a paid dependency and a second jurisdiction question, so it belongs here or later, not earlier).

## 10. Risks

- **Cost creep**, the main way this goes wrong; mitigated by depth defaults and caps.
- **A public login that can spend money.** Registration is closed; 2FA is still open in TASKS.md.
- **Model drift.** IDs and prices change monthly; roster in a file; a 404 drops a member instead of failing the run.
- **Host trust and quantisation.** R2 hosts are third parties, and some serve quantised weights (the ZDR list shows e.g. fp4), which can lower quality; check per host.
- **Chair bias.** Opus chairing a council it sits on may favour its own seat; anonymisation helps, and phase 1 includes a chair swap on a few runs.
- **Thin evidence base for this use case.** The design leans on what has support (independence, blinding, grounding, heterogeneity, receipts) and leaves out what doesn't (multi-round debate, rank-as-verdict).

## 11. Decisions needed

1. **Route classes.** May private notes go to R2 (Western hosts serving DeepSeek/Kimi/GLM under zero retention), or only R1? May public evidence ever go to R3 (the labs' own endpoints)? Defaults: private → R1 only; R3 never.
2. **R2 allow-list.** Which hosts do you accept?
3. **Spend caps.** $1.50 per run and $10 per day suggested.
4. **Phase 0.** Send me three to five bad queries and I'll run the diagnosis.
5. **Per-member-agent arm.** It needs you to create the agents in the UI; do you want it in the trial?
6. **Chair.** Fixed Opus 5, or rotate?
7. **Where reports go.** Run logs stay on the NAS; saving them into the vault would need a write path that the second-brain services deliberately lack.

## Sources

- Karpathy, [llm-council](https://github.com/karpathy/llm-council); Perplexity, [Introducing Model Council](https://www.perplexity.ai/hub/blog/introducing-model-council)
- Wang et al., [Mixture-of-Agents](https://arxiv.org/abs/2406.04692); Li et al., [Rethinking Mixture-of-Agents](https://arxiv.org/abs/2502.00674); Smit et al., [Should we be going MAD?](https://arxiv.org/abs/2311.17371); [If Multi-Agent Debate is the Answer, What is the Question?](https://arxiv.org/abs/2502.08788); [Artificial Hivemind](https://arxiv.org/abs/2510.22954); [Self-Preference Bias in LLM-as-a-Judge](https://arxiv.org/abs/2410.21819); [LLMs-as-Judges survey](https://arxiv.org/abs/2412.05579)
- [LibreChat agents docs](https://www.librechat.ai/docs/features/agents); [LibreChat interface config (`multiConvo`)](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/interface); [LibreChat #3659](https://github.com/danny-avila/LibreChat/issues/3659)
- [Big-AGI #892](https://github.com/enricoros/big-AGI/issues/892); the original's Big-AGI, Onyx and licensing sources are listed in the archived copy
- [OpenRouter provider routing](https://openrouter.ai/docs/features/provider-routing); OpenRouter model and zero-retention endpoint listings, fetched 2026-09-19
