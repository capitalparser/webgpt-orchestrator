---
name: webgpt-orchestrator
description: Use when automating software delivery through WebGPT, Codex, and Claude with GitHub pull requests as the implementation boundary.
---

# WebGPT Orchestrator

Orchestrate WebGPT, Codex, and Claude into an autonomous delivery loop. The default
workflow is **Forge Loop**: plan, implement through GitHub, verify an immutable PR,
review, hand off feedback, and repeat until the merge gate is ready or a stop condition
is reached.

Use this skill when WebGPT is implementing through its existing GitHub connector and the
coordinator agent — Claude Code or Codex CLI, either has equal authority to run this skill —
must drive the WebGPT conversation and verify the resulting pull request.

## Boundary

- WebGPT uses GitHub only. Do not enable or request the Full harness, OpenAI Tunnel, local
  shell, or local filesystem tools for WebGPT.
- WebGPT never creates repositories. When a new repository is required, the coordinator
  agent creates it via `gh repo create` (private by default; `--public` must be explicit)
  before starting the WebGPT conversation, and tells WebGPT which repository to use.
- The coordinator agent tests only the immutable PR head SHA in a temporary detached
  checkout.
- Never run a test command through a shell string. Commands come from a JSON array
  configuration.
- In the Forge Loop, PR test comments publish automatically (`--post-comment` is
  always passed) — redaction already strips secrets and local paths, so this is no longer a
  separate human-confirmation gate.
- This skill does not merge pull requests. A protected `main` and required checks remain
  the merge gate.
- A hard cap (`max_iterations`, default 5) is enforced by `forge_loop.py` itself. Once hit,
  the cycle returns `BLOCKED_MAX_ITERATIONS` and the agent must stop and report to the
  user rather than keep retrying.
- No concurrent Forge Loop runs with the same state filename: the ego-browser task space
  is derived only from the
  `--state` file's basename, not its directory, so two concurrent cycles that happen to
  share a `--state` filename (e.g. both using the generic `forge.json` from the examples
  below) collide on the same browser conversation. Because step 2 of the Forge Loop
  skips re-verifying the reasoning tier/connector on a reused conversation, and
  `--post-comment` is always on, this can silently cross-contaminate: step 4 may extract
  the *other* cycle's PR URL, and step 5 will then test and auto-publish a redacted
  comment on a PR in a completely unrelated repository. Always give concurrent cycles
  distinct, descriptive `--state` filenames.

## Commands

The skill is callable from Codex Desktop, Codex CLI, Claude Code, and Orca-managed CLI
terminals by mentioning `$webgpt-orchestrator`. In clients that provide a `/skills` picker,
select `webgpt-orchestrator` there. Arbitrary `/webgpt` slash commands are not registered by a
plugin; the following phrases are conversation aliases after the skill is loaded:

```text
plan with WebGPT
implement through WebGPT
status <PR>
test <PR>
iterate with WebGPT
finish the Forge Loop
```

`plan`/`implement`/`iterate`/`finish` start or continue the Forge Loop described
below; `status <PR>` and `test <PR>` are narrower one-shot calls into `status`/`test`
and do not themselves drive the WebGPT conversation.

From the plugin directory:

```text
python3 scripts/forge_loop.py status --pr <PR URL or number>
python3 scripts/forge_loop.py test --pr <PR URL or number> --commands-json <commands.json> --result <result.json>
python3 scripts/forge_loop.py test --pr <PR URL or number> --commands-json <commands.json> --result <result.json> --post-comment
python3 scripts/forge_loop.py handoff --result <result.json>
python3 scripts/forge_loop.py iterate --result <result.json>
python3 scripts/forge_loop.py forge --request "<request>" --state <state.json> [--new-repo <name>] [--public] [--max-iterations N]
python3 scripts/forge_loop.py forge --pr <owner/repository#number> --commands-json <commands.json> --state <state.json> [--post-comment]
```

`commands.json` must be a JSON array of argv arrays, for example:

```json
[["pytest", "-q"], ["python3", "-m", "compileall", "src"]]
```

`forge` is the canonical subcommand. The legacy `cycle` spelling remains an alias so
existing local automation does not break during migration.

`--new-repo <name>` provisions a brand-new GitHub repository through `gh repo create`
before generating the WebGPT prompt (private by default; pass `--public` for a public
repo). It is only valid together with `--request`; combining it with `--pr` is rejected.

## Forge Loop (default workflow)

Run this procedure directly — do not stop for human confirmation between steps unless a
stop condition below applies:

1. `python3 scripts/forge_loop.py forge --request "<request>" --state <state.json> [--new-repo <name>] [--public] [--max-iterations N]`.
   This provisions a new repository first when `--new-repo` is given, and produces
   `state.json` with `status: AWAITING_WEBGPT_PR`, `webgpt_prompt`, and a deterministic
   `task_space` name derived from the state file (e.g. `forge.json` becomes
   `webgpt-orchestrator:forge`) — reuse that same ego-browser task space on every later step so
   the WebGPT conversation and its context stay intact across iterations. Use a unique,
   descriptive `--state` filename per concurrent cycle — see Boundary for what goes wrong
   if two cycles collide on the same task space.
2. Using ego-browser (`ego-browser nodejs <<'EOF' ... EOF` via Bash), open or reuse the
   task space's chatgpt.com tab.
   - Only on a genuinely new conversation: select the High reasoning tier in the model
     picker (default for the whole loop — see the reasoning-tier note below), and confirm
     the GitHub connector is attached to the conversation (attach it if it is not). Skip
     re-selecting these on later iterations that reuse the same conversation, unless the
     connector is observed to be missing.
3. Type the current prompt into the conversation and submit — `webgpt_prompt` on the
   first turn, `webgpt_handoff` on every retry turn.
4. Observe with `snapshotText`/`wait` until the reply is finished (there is no fixed
   selector for this — read the current page state each round and judge). Once finished,
   classify it into exactly one of these three cases — the first two can happen on the very
   first reply, before `forge --pr` (step 5) has ever been called:
   - **Connector access denied.** WebGPT reports it cannot access the repository through
     its GitHub connector (for example, because a GitHub App install is scoped to specific
     repositories and doesn't include a newly-created one): stop, do not retry, and ask the
     user to grant connector access.
   - **No usable PR reference.** The reply is finished but contains neither a
     `github.com/<owner>/<repo>/pull/<n>` link nor a `PR_URL: <url>` trailer, and it isn't
     connector-access denial either — e.g. a clarifying question, an unrelated error, or an
     ambiguous answer. Ask WebGPT once, in the same conversation, to restate
     `PR_URL: <url>`. If the follow-up reply still doesn't resolve to a PR reference or a
     connector-access denial, stop and report `BLOCKED` to the user — do not keep
     observing/looping past this one retry.
   - **PR reference found.** Extract the PR ref and continue to step 5.
5. `python3 scripts/forge_loop.py forge --pr <owner/repo#n> --commands-json <commands.json> --state <state.json> --post-comment`.
6. If the result is `AWAITING_WEBGPT_FIX`: type the new `webgpt_handoff` text back into the
   same conversation (return to step 3).
7. If the result is `READY_TO_MERGE`: stop and report to the user. Do not merge.
8. If the result is `BLOCKED_MAX_ITERATIONS`: stop and report to the user — do not keep
   retrying past the cap.

### Reasoning tier: High by default, Pro only for planning

Default the whole loop — the implement turn and every iterate/handoff turn — to the High
reasoning tier, not Pro. This was Pro by default in an earlier version of this skill;
a live run showed Pro taking 20+ minutes per turn on a trivial one-word fix, against
1-2 minutes at High for the same request, with no difference in correctness (High
correctly detected and refused an inconsistent request in the same way Pro would have).
If a request genuinely needs deep up-front design work, hold that planning exchange at
Pro tier as its own turn in the same conversation before sending `webgpt_prompt`, then
switch to High before continuing the loop — do not run the implement/iterate turns at
Pro by default.

## Stop conditions

Stop and report `BLOCKED` if `gh` authentication, PR metadata, checkout, command
configuration, or the isolated test setup cannot be verified. Do not guess a branch or use
a local tracking ref as a substitute for the PR head SHA. A missing or unreadable
`--state` file also surfaces as `BLOCKED` — this list of causes is illustrative, not
exhaustive.

On any ego-browser hard stop — "user is controlling," a login prompt, or a captcha — do not
retry the browser action. Hand off the task space to the user and wait for explicit
confirmation before resuming, per the ego-browser skill's own control-handoff rules.

## MCP server surface (unchanged)

When the plugin MCP server is enabled, the WebGPT-facing tool surface is deliberately
small: `webgpt_pr_status`, `webgpt_pr_test`, and `webgpt_pr_handoff`. Test commands are
selected from the checked-in `smoke` or `default` profiles. No generic shell, filesystem,
patch, deployment, or merge tool is exposed. This surface is the least-privilege MCP
entrypoint to Forge Loop for consumers that speak MCP rather than Bash/ego-browser.
