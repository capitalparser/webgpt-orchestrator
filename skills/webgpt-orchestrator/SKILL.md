---
name: webgpt-orchestrator
description: Use for repository-scoped WebGPT planning, research, review, project context management, and GitHub PR delivery coordinated by Codex or Claude.
---

# WebGPT Orchestrator

Orchestrate WebGPT, Codex, and Claude into an autonomous delivery loop. The default
workflow is **Forge Loop**: plan, implement through GitHub, verify an immutable PR,
review, hand off feedback, and repeat until the merge gate is ready or a stop condition
is reached.

Use this skill when the coordinator agent — Claude Code or Codex CLI — drives WebGPT
for repository-scoped work. Start with project discovery below for planning, research,
review, and implementation. Use Forge Loop when the intended artifact is a GitHub PR;
planning or source-management tasks do not require creating a PR or Forge state.

## First use: check the repository's Project

When invoked to do repository-scoped work, resolve the target `owner/repo` and run
`python3 scripts/project_context.py lookup --repository <owner/repo>` before the first
WebGPT turn. Follow [Project setup and sources](references/project-context.md) to check the
actual ChatGPT account/workspace, reuse a matching Project, or create one if none exists.
This is a required initial step, including for work on this orchestrator repository.

A missing local record means **not linked yet**, not that no Project exists. Search existing
Projects, including descriptive names that differ from the repository name, before creating.
Keep the existing name when linking an established Project. Once the task is authorized and
the repository is clear, ordinary project setup does not require a separate approval turn.
Do not create a Project merely because this skill was mentioned, read, or edited.

On later invocations, reuse the saved mapping and verify it is still accessible and belongs
to the intended work. New tasks get new chats inside that Project; retries reopen the
recorded chat. Project setup does not authorize sending an unconfirmed WebGPT question.

## Boundary

- WebGPT uses GitHub for repository reads and writes. The coordinator may add task-relevant
  project sources through the browser. Do not enable or request the Full harness, OpenAI
  Tunnel, local shell, or local filesystem tools for WebGPT.
- WebGPT never creates repositories. When a new repository is required, the coordinator
  agent creates it via `gh repo create` (private by default; `--public` must be explicit)
  before starting the WebGPT conversation, and tells WebGPT which repository to use.
- The coordinator agent tests only the immutable PR head SHA in a temporary detached
  checkout.
- Never run a test command through a shell string. Commands come from a JSON array
  configuration.
- Loading, referencing, or mentioning this skill alone does **not** authorize creating a
  Forge state or sending a WebGPT prompt. For an invoked repository task, project discovery
  may happen before the question is finalized; source upload or project creation requires
  a clear authorized task and repository. Before every new initial handoff, stay
  in the coordinator's CLI/chat conversation and confirm the exact **WebGPT question** with
  the user: the action or answer requested from WebGPT and the expected artifact or reply.
  If the user only asks to start or reference the skill, ask what WebGPT should be asked;
  do not draft a browser prompt from coordinator assumptions. Pass the confirmed wording as
  `--webgpt-question`.
- Before the first WebGPT prompt, the coordinator must create a **confirmed intent brief**
  and pass it to `forge --request` as `--intent-brief`. The brief states the user's goal,
  success criteria, scope or non-goals, constraints, and validation. The coordinator shows
  that brief and the WebGPT question to the user before handoff. A current user request
  counts as confirmation only when it explicitly supplies or endorses every material field
  and the exact question; otherwise ask the smallest question that resolves the uncertainty.
  Never invent confirmation or send an unconfirmed question or brief to WebGPT.
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
  below) collide on the same browser conversation. Because a reused conversation can retain
  the wrong model or connector, and
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
python3 scripts/forge_loop.py forge --request "<request>" --webgpt-question "<confirmed question>" --intent-brief "<confirmed brief>" --state <state.json> [--new-repo <name>] [--public] [--max-iterations N]
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

After the CLI question and intent are confirmed, run this procedure directly — do not stop
for another human confirmation between steps unless a stop condition below applies:

0. **CLI question-and-intent preflight — before `forge --request` or prompt submission.**
   Keep the conversation in the coordinator's CLI/chat and write a short, user-visible
   delegation brief with: (a) the exact question or task for WebGPT and its expected
   artifact/reply, then (b) goal, success criteria, scope/non-goals, constraints, and
   validation. Show the whole brief to the user. If the current user request clearly
   supplies or endorses every material field and exact question, restating it is enough and
   the request itself is the confirmation; do not create a redundant approval turn. If the
   user merely asks to start/reference the skill, or any material field is ambiguous, ask
   one focused question and wait. Use the confirmed wording verbatim as
   `--webgpt-question` and `--intent-brief`; do not substitute coordinator assumptions.
1. `python3 scripts/forge_loop.py forge --request "<request>" --webgpt-question "<confirmed question>" --intent-brief "<confirmed brief>" --state <state.json> [--new-repo <name>] [--public] [--max-iterations N]`.
   This provisions a new repository first when `--new-repo` is given, and produces
   `state.json` with `status: AWAITING_WEBGPT_PR`, `webgpt_prompt`, and a deterministic
   `task_space` name derived from the state file (e.g. `forge.json` becomes
   `webgpt-orchestrator:forge`) — reuse that same ego-browser task space on every later step so
   the WebGPT conversation and its context stay intact across iterations. Use a unique,
   descriptive `--state` filename per concurrent cycle — see Boundary for what goes wrong
   if two cycles collide on the same task space.
2. Using ego-browser (`ego-browser nodejs <<'EOF' ... EOF` via Bash), open or reuse the
   task space's chatgpt.com tab, completing **First use** and the linked project setup
   procedure below. Attach the verified mapping to the state with `project_context.py attach`. For a repository-scoped new task, create the conversation inside that repository's
   ChatGPT Project; for a retry or resumed task, reopen its recorded conversation.
   - Use a standard **Chat** conversation, never ChatGPT Work or Codex. Before **every**
     `webgpt_prompt` or `webgpt_handoff` submission, verify the surface is Chat and inspect
     the model picker. If the tab is in Work, return to the repository's Project and open
     the task's standard Chat conversation there; do not send the WebGPT prompt from Work.
   - In Chat, select the model-picker button whose whitespace-normalized text is **`6 Pro`**.
     The current picker exposes this as `6` followed by `Pro` on the next line. Verify that
     exact normalized label before sending; do not treat `High`, `Extra High`, automatic
     reasoning, or another Pro model as equivalent.
   - If standard Chat or `6 Pro` is not offered to the signed-in account, stop with
     `BLOCKED_MODEL_UNAVAILABLE` and report it to the user. Do not submit the prompt from
     Work or with a fallback model or reasoning tier.
   - On a genuinely new conversation, also confirm the GitHub connector is attached
     (attach it if it is not). On a reused conversation, confirm it again only if it is
     observed to be missing.
3. Type the current prompt into the conversation and submit — `webgpt_prompt` on the
   first turn, `webgpt_handoff` on every retry turn.
4. Observe with the current ego-browser skill's `snapshot()` and bounded waits until the reply is explicitly finished (there is no
   fixed selector for this — read the current page state each round and judge). The WebGPT
   response deadline is unbounded: never set or infer a cumulative or wall-clock timeout.
   Use only short, bounded `wait()` calls (at most 60 seconds per call) so each round can
   re-check the page, then keep observing. A quiet or slowly streaming reply is not
   finished, failed, or `BLOCKED` merely because time has elapsed. Once finished, classify
   it into exactly one of these three cases — the first two can happen on the very first
   reply, before `forge --pr` (step 5) has ever been called:
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
   same conversation (return to step 3). Do not ask the user to reconfirm the delegation
   brief for a test-only retry. Return to step 0 only if the requested fix changes the
   WebGPT question, goal, success criteria, scope, constraints, or validation.
7. If the result is `READY_TO_MERGE`: stop and report to the user. Do not merge.
8. If the result is `BLOCKED_MAX_ITERATIONS`: stop and report to the user — do not keep
   retrying past the cap.

### Repository projects and conversation continuity

Follow [Project setup and sources](references/project-context.md) for discovery, creation,
local mapping commands, source maintenance, limits, and conversation handoff. All WebGPT
browser entrypoints in this skill use that procedure. One-shot GitHub `status`/`test` calls
and MCP PR tools do not open ChatGPT and do not need project setup.

Before every submission, verify the Project, Chat surface, model, and connector in the UI.
A saved mapping is a routing aid, not proof of current account access or Project membership.
If discovery is ambiguous or the Project is unavailable, report
`BLOCKED_PROJECT_UNAVAILABLE`; do not silently use a global new chat or create duplicates.
A standalone chat is allowed only for work with no project scope or an explicit user exception.

### Required Chat model: `6 Pro`

Use standard Chat with `6 Pro` for every WebGPT turn in a Forge Loop: planning,
implementation, PR-test handoff, and retry. Recheck the Chat surface and model before each
submission, including submissions in a reused conversation. The loop must not consume a
ChatGPT Work/Codex allowance or a different model's allowance through a silent fallback. If
the required Chat surface or model is unavailable, stop before sending the prompt.

## Stop conditions

Stop and report `BLOCKED` if `gh` authentication, PR metadata, checkout, command
configuration, or the isolated test setup cannot be verified. Do not guess a branch or use
a local tracking ref as a substitute for the PR head SHA. A missing or unreadable
`--state` file also surfaces as `BLOCKED` — this list of causes is illustrative, not
exhaustive.

Stop and report `BLOCKED_MODEL_UNAVAILABLE` if the standard Chat surface or model picker
cannot be verified, or if it does not offer `6 Pro`. Do not use ChatGPT Work,
Codex, or a fallback model.

Stop and report `BLOCKED_DELEGATION_UNCONFIRMED` before creating a Forge state or
sending a first WebGPT prompt if the WebGPT question or intent brief is missing,
lacks a material field, or has not been confirmed by the user. Do not treat a coordinator's
inference as confirmation.

On any ego-browser hard stop — "user is controlling," a login prompt, or a captcha — do not
retry the browser action. Hand off the task space to the user and wait for explicit
confirmation before resuming, per the ego-browser skill's own control-handoff rules.

## MCP server surface (unchanged)

When the plugin MCP server is enabled, the WebGPT-facing tool surface is deliberately
small: `webgpt_pr_status`, `webgpt_pr_test`, and `webgpt_pr_handoff`. Test commands are
selected from the checked-in `smoke` or `default` profiles. No generic shell, filesystem,
patch, deployment, or merge tool is exposed. This surface is the least-privilege MCP
entrypoint to Forge Loop for consumers that speak MCP rather than Bash/ego-browser.
