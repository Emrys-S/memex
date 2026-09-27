# Remote MCP connector: getting Cowork and Projects onto the second brain

Written 2026-08-25 in Claude's "Second Brain" claude.ai Project (chat), not originally as a file anywhere in this repo or on the NAS — materialized here 2026-09-15 after Code went looking for it and couldn't find it. Follows on from `librechat-model-council-design.md` (2026-08-24, same Project; the original is still only in the Project — a rebuilt, researched version lives at `librechat-model-council-design.md`, 2026-09-19) — that doc designs the council *inside* LibreChat; this one is about a different consumer of `second-brain-mcp`: Cowork sessions and Claude Projects (i.e., Claude's own hosted surfaces, not the NAS-hosted LibreChat instance). Per the same working-principle split used in that doc — hands-on NAS execution stays with Claude Code over SSH, Cowork's role is planning — this doc is planning only. The actual exposure work (step 1 below) belongs in a Claude Code/nas-ops session.

## Why this doesn't work today

`second-brain-mcp` is deployed and proven (TASKS.md, 2026-08-15), but it's only reachable over Tailscale — that's sufficient for LibreChat, which runs on the same NAS. Cowork and claude.ai are hosted by Anthropic and can only attach to *remote* MCP servers reachable over the public internet from Anthropic's IP ranges, using OAuth (client ID/secret) as the supported auth flow. A Tailscale-only endpoint is invisible to them regardless of how the connector is configured client-side.

So this is a networking/auth problem first, a Claude-settings problem second.

## What's required

1. **A public HTTPS endpoint for `second-brain-mcp`**, scoped as narrowly as possible — not the whole NAS. A Cloudflare Tunnel or Tailscale Funnel pointed at just the MCP service (not other Docker services on the box) keeps the blast radius small if credentials ever leak.
2. **Real auth in front of it.** OAuth is what Claude's custom-connector flow expects natively; at minimum, an API-key/bearer-token layer plus IP-allowlisting Anthropic's published ranges as defense in depth. This is the same class of decision this project has already treated deliberately elsewhere (the Swiss-jurisdiction VPS candidate, keeping Boox notes out of prompts sent to non-Western-hosted council models) — worth a real decision, not a default.
3. **Registering it as a custom connector** on the Claude account: Settings → Connectors → "+" → Add custom connector → server URL (+ OAuth credentials if configured). This is an account-level action, done once.
4. **Enabling it per-conversation** via the "+" button → Connectors. Confirmed working the same way across claude.ai, Cowork, and Claude Desktop. No documentation found of connectors being scoped to a single Project — once registered at the account level, it's available to toggle on anywhere, including inside this Project.
5. **Project custom instructions** telling Claude to reach for `query_second_brain` by default when working in this Project, so it doesn't require toggling the connector on manually every session.

Steps 3–5 can be driven directly from Cowork/claude.ai (including live, via Chrome browser control) once steps 1–2 are done. Steps 1–2 should happen in Claude Code against nas-ops, consistent with how `second-brain-mcp` and everything else in the pipeline was built.

## Alternative considered: device-bridge instead of public exposure

If exposing anything publicly is unwelcome, a Cowork session connected to Emrys's Mac (the remote-devices bridge) could in principle reach the NAS over the same Tailscale network the Mac is already on, without a public connector at all — e.g. running a script against `second-brain-mcp`'s local endpoint from a Mac-side shell. Untested: the bridge's shell access follows the organization's network egress allowlist, which may permit no outbound network access at all. Worth a five-minute live test before investing in the public-exposure path, since if it works it's strictly simpler and avoids the auth/exposure decision entirely.

## Open flag: LibreChat results quality

Emrys noted (2026-08-25) that LibreChat's results haven't been that helpful so far — not yet diagnosed (retrieval relevance vs. synthesis quality vs. UI friction vs. something else). This bears directly on deferred architecture decision #3 in nas-ops CLAUDE.md (LibreChat as daily-driver) and on the "is LibreChat the right foundation" section of the model-council doc, which already flagged Onyx and Big-AGI as alternatives. Worth diagnosing *before* sinking more effort into either the council build-out or this connector work, since both assume LibreChat (or at least `second-brain-mcp`'s current retrieval behavior) is already producing good results to build on top of — if the underlying retrieval is the actual problem, a nicer front end or a second consumer of the same tool won't fix it. Flagging here so it isn't lost; to pick up in a future session.

## Open questions

- OAuth vs. API-key-plus-allowlist for the public endpoint — which is actually simpler to stand up given the existing SOPS+age secrets pattern?
- Does the device-bridge route actually have outbound network access in this org's egress settings? (cheap to test)
- Is "not helpful" a retrieval problem (chunking, embedding quality), a synthesis problem, or something about how the query is being asked? Needs a few concrete bad-result examples to diagnose rather than reasoning about it abstractly.
- Also relevant now: the restricted-work-computer browser-access question (flagged in COWORK-NOTES.md, 2026-09-15) — a public, browser-reachable connector may matter more than Tailscale-only access if a locked-down work machine is a real access path that needs to work.

## Note on this file's origin

This design doc, and at least `librechat-model-council-design.md`, were written in Claude's hosted "Second Brain" Project (claude.ai chat/Cowork), which has its own separate doc store — not this git repo, not anywhere on the NAS filesystem. Nothing in that Project automatically syncs to `nas-ops`. Until this file was materialized here, `CLAUDE.md` referenced it by name with no way for a Claude Code session to actually read it. Worth deciding whether other design docs still living only in that Project (check `librechat-model-council-design.md`, `karpathy-llm-wiki-implications.md`) should get the same treatment, or whether `COWORK-NOTES.md` should just carry a standing note that referenced-but-unlocated filenames may mean "ask Cowork to materialize it" rather than "search harder."
