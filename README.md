# webgpt-pr-cycle

[![test](https://github.com/capitalparser/webgpt-pr-cycle/actions/workflows/test.yml/badge.svg)](https://github.com/capitalparser/webgpt-pr-cycle/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Let ChatGPT Web ("WebGPT") implement a GitHub pull request through its own GitHub
connector, while a local coordinator agent — Claude Code or Codex CLI, either works,
neither is privileged — drives the conversation, tests the resulting PR in an isolated
checkout, and pushes failures straight back into the same chat. Runs unattended until the
PR passes or a hard iteration cap is hit. Never merges.

```
you: "add dark mode" ──▶ coordinator agent ──▶ ChatGPT Web (GitHub connector)
                              │                        │
                              │◀──── PR opened ─────────┘
                              │
                        isolated test
                         checkout
                              │
                      pass ───┴─── fail
                       │             │
                  report to     push failure
                    human        back into chat
                  (you merge)     (repeat)
```

## Why this exists

ChatGPT Web with the GitHub connector is a genuinely capable coding agent — it can read a
repo, write a patch, open a PR. What it doesn't have is a sandbox to *run* anything. So the
loop of "ask WebGPT to implement → copy the PR URL → test it locally → copy the failure
back → ask WebGPT to fix it → repeat" is real, tedious, human-in-the-middle work. This
project automates that loop end to end: a coordinator agent drives the browser, runs the
tests, and relays results, so the human only shows up to review the final PR.

It does **not** give WebGPT any new capability. WebGPT still only has its GitHub
connector — no shell, no filesystem, no ability to merge or create repositories. The
coordinator agent is the only thing that gained anything: a way to type into a chat box and
read the reply.

## How it works

Three pieces, each with one job:

- **`scripts/pr_cycle.py`** — the only part that touches git, GitHub, and the filesystem.
  Resolves PR metadata, clones the immutable PR head SHA into a throwaway directory, runs
  your test commands as argv arrays (never a shell string), redacts secrets/local paths
  from anything that becomes visible, and persists a small JSON state machine
  (`AWAITING_WEBGPT_PR → AWAITING_WEBGPT_FIX/READY_TO_MERGE`, or `BLOCKED_MAX_ITERATIONS`).
  It never touches a browser and never talks to WebGPT directly.
- **[ego-browser](https://lite.ego.app/ko)-driven conversation** — a coordinator agent
  (any LLM agent with a Bash tool and the `ego-browser` CLI installed) opens or reuses a
  browser tab to chatgpt.com, types the prompt `pr_cycle.py` generated, and reads WebGPT's
  reply. This is deliberately *not* a scripted browser automation — ChatGPT's UI changes,
  streaming states vary, and WebGPT occasionally needs a judgment call (a clarifying
  question, a connector-permission problem). An LLM observing the page each round handles
  that; a hardcoded selector script would not.
- **`skills/webgpt-pr-cycle/SKILL.md`** — the runbook the coordinator agent follows. It's
  the actual spec for the loop: when to stop, when to retry, when to ask the human. See
  it for the literal step-by-step procedure; this README explains the shape, SKILL.md is
  the contract.

No prior repo or framework was used as a reference for this loop — it's built directly
from `pr_cycle.py`'s state machine plus ego-browser's documented primitives
(`snapshotText`, `click`, `typeText`, task spaces).

## Status

Live-tested once, end to end, against a real GitHub repo and a real ChatGPT Pro account:
WebGPT correctly *refused* to open a PR when the target file didn't exist on the branch it
was told to use (reporting `PR_URL: NOT_CREATED` with an explanation instead of guessing),
and after the underlying repo state was fixed, opened a clean one-line-diff PR that the
loop tested, commented on, and marked `READY_TO_MERGE` — see "Reasoning tier" below for
what that run also taught us about model settings. This is young software: one real run,
a thorough unit test suite (31 tests) around the deterministic parts, and multiple rounds
of independent code review on every commit. Read `SKILL.md`'s Boundary and Stop conditions
sections before pointing it at anything you care about.

## Prerequisites

- **`gh` CLI**, authenticated (`gh auth status`), with at least `repo` scope. Add
  `delete_repo` if you want to clean up test repos you create with `--new-repo`.
- **[ego-browser](https://lite.ego.app/ko)** (a CLI browser-automation tool for LLM
  agents) installed and logged into chatgpt.com in the profile it drives. The GitHub
  connector needs to have been
  authorized on that account at least once (Settings → Connectors in ChatGPT, or attach it
  from the `+` menu in any chat — the coordinator agent will do the latter automatically).
- **Python 3.10+** (stdlib only — no dependencies to install for `pr_cycle.py` itself).
  `pytest` if you want to run the test suite.
- A coordinator agent session (Claude Code, Codex CLI, or anything else with Bash access
  and the ability to follow `SKILL.md`) actually driving the loop. `pr_cycle.py` alone
  only handles the git/test/state half — the browser half needs an agent in the loop.

## Quick start

Clone this repo, then from its root:

```bash
# Against an existing repository
python3 scripts/pr_cycle.py cycle \
  --request "In owner/repo, on main, add a CONTRIBUTING.md covering PR conventions." \
  --state /tmp/webgpt-cycles/contributing-doc.json

# Or have it provision a brand-new (private by default) repository first
python3 scripts/pr_cycle.py cycle \
  --request "Scaffold a minimal FastAPI app with a /healthz endpoint and one test." \
  --state /tmp/webgpt-cycles/new-fastapi-app.json \
  --new-repo my-new-fastapi-app
```

Then hand that off to a coordinator agent with: *"Follow `SKILL.md`'s autonomous loop for
this cycle."* From there it runs unattended — opens the browser, drives WebGPT, tests each
PR, retries on failure — until it reports back `READY_TO_MERGE`, `BLOCKED`, or
`BLOCKED_MAX_ITERATIONS`. You still do the merge yourself.

**Give every concurrent cycle a distinct `--state` filename.** The browser conversation is
keyed off the state file's basename; two cycles sharing one (e.g. both using the generic
`cycle.json` from examples) will silently collide on the same chat and can cross-publish a
comment on the wrong repository's PR. See `SKILL.md`'s Boundary section.

### What the state file actually looks like

Real shape from the first live run (owner/repo and SHAs genericized below; field names and
structure are exactly what `pr_cycle.py` emits). `cycle --request` produces:

```json
{
  "status": "AWAITING_WEBGPT_PR",
  "iteration": 0,
  "max_iterations": 5,
  "task_space": "webgpt-pr-cycle:example-fix",
  "next_action": "Ask WebGPT to implement and return a PR URL.",
  "webgpt_prompt": "Implement this request through your existing GitHub connector, open a PR, and return the exact PR URL and head SHA. Do not merge it.\n\n<your request>\n\nReply with the pull request URL on its own trailing line as `PR_URL: <url>`."
}
```

After the coordinator agent extracts a PR from WebGPT's reply and runs `cycle --pr ...
--post-comment`, a failing test looks like:

```json
{
  "status": "AWAITING_WEBGPT_FIX",
  "iteration": 1,
  "next_action": "Send the failure handoff to WebGPT and ask it to update the PR.",
  "report": { "status": "TEST_FAILED", "commands": [{ "argv": ["pytest", "-q"], "exit_code": 1, "duration_seconds": 0.02 }] },
  "webgpt_handoff": "<!-- webgpt-pr-cycle -->\n## PR test: TEST_FAILED\n...\n\nPush a fix, then reply with the pull request URL on its own trailing line as `PR_URL: <url>`."
}
```

`webgpt_handoff` is typed straight back into the same ChatGPT conversation. Once the test
passes, `status` becomes `READY_TO_MERGE` and `next_action` becomes
`"Review branch protection and merge the PR."` — the tool stops there.

## The autonomous loop

1. `cycle --request` creates `state.json`, optionally provisioning a new repo first
   (`--new-repo`, private unless `--public`), and generates the prompt WebGPT will see.
2. The coordinator agent opens/reuses a browser tab, selects the High reasoning tier and
   attaches the GitHub connector (only on a genuinely new conversation).
3. Types the prompt, submits, waits for the reply — no fixed timeout; the agent judges
   when generation has actually finished.
4. Classifies the finished reply: connector access denied → stop and ask the human to
   grant access; no PR reference found → ask once for a `PR_URL: <url>` restatement, then
   give up and report `BLOCKED`; PR found → continue.
5. `cycle --pr <owner/repo#n> ... --post-comment` tests the immutable PR head SHA in an
   isolated checkout and publishes the result as an actual PR comment.
6. Failure → the failure output (redacted) gets typed back into the same conversation as a
   handoff, and the loop returns to step 3. Success → stop, report `READY_TO_MERGE`,
   don't merge. Hit `--max-iterations` (default 5) → stop, report
   `BLOCKED_MAX_ITERATIONS`, don't keep retrying.

## Reasoning tier: High by default, not Pro

An earlier version of this project defaulted every turn to ChatGPT's Pro reasoning tier.
The first live run showed Pro taking 20+ minutes on a one-word fix; the same request at
High finished in 1-2 minutes with no loss of correctness — High still correctly refused an
inconsistent request instead of guessing. The loop now defaults to High end to end. If a
request genuinely needs deep up-front design thinking, hold that as its own Pro-tier
exchange in the same conversation before the implement/iterate turns, which stay at High.

## Safety boundaries

- WebGPT gets no new capability — GitHub connector only, ever. No shell, no filesystem,
  no Full harness, no OpenAI Tunnel.
- WebGPT never creates repositories. `--new-repo` provisions them via `gh repo create`
  (private by default) *before* WebGPT is even prompted; WebGPT is told which repo to use.
- Tests run only against the immutable PR head SHA, in a fresh temporary clone, deleted
  after. Test commands are JSON argv arrays — never a shell string.
- Secrets, tokens, and local absolute paths are redacted (`redact_text`) before anything
  reaches a GitHub comment, a chat message, or stdout.
- This tool never merges. Branch protection and required checks remain the actual gate.
- `max_iterations` (default 5) is enforced inside `pr_cycle.py` itself — a misbehaving
  coordinator agent can't loop forever regardless of whether it follows the runbook.

## CLI reference

```text
python3 scripts/pr_cycle.py status --pr <PR URL or number>
python3 scripts/pr_cycle.py test --pr <PR URL or number> --commands-json <commands.json> --result <result.json> [--post-comment]
python3 scripts/pr_cycle.py handoff --result <result.json>
python3 scripts/pr_cycle.py iterate --result <result.json>
python3 scripts/pr_cycle.py cycle --request "<request>" --state <state.json> [--new-repo <name>] [--public] [--max-iterations N]
python3 scripts/pr_cycle.py cycle --pr <owner/repository#number> --commands-json <commands.json> --state <state.json> [--post-comment]
```

`commands.json` is a JSON array of argv arrays:

```json
[["pytest", "-q"], ["python3", "-m", "compileall", "src"]]
```

`--new-repo <name>` is only valid with `--request` (rejected in combination with `--pr`).

## Testing

```bash
python3 -m pytest tests/test_pr_cycle.py -v
```

31 tests, all against fake subprocess runners — no network, no real `gh`/`git` calls, no
browser. Covers PR parsing, redaction, isolated-checkout behavior, the full cycle state
machine (including the `max_iterations` cap and repo-provisioning failure paths), and
CLI argument validation.

## MCP server (optional, separate surface)

`scripts/mcp_server.py` exposes a deliberately narrow MCP tool surface —
`webgpt_pr_status`, `webgpt_pr_test`, `webgpt_pr_handoff` — for callers that speak MCP
instead of driving `pr_cycle.py`/ego-browser directly. It shares no code path with the
autonomous loop above and doesn't expose a generic shell, filesystem, patch, or merge tool.
Configured via `.mcp.json`.

## Project structure

```
scripts/pr_cycle.py          deterministic core: git, gh, tests, redaction, state machine
scripts/mcp_server.py        narrow MCP surface (optional, separate from the main loop)
skills/webgpt-pr-cycle/SKILL.md   the runbook a coordinator agent follows
tests/test_pr_cycle.py       31 tests against fake runners
commands.example.json        example test-command profile
commands.smoke.json          minimal smoke-test profile
.codex-plugin/plugin.json    Codex plugin manifest
.mcp.json                    MCP server registration
```

## Known limitations

- One cycle at a time — no concurrency support beyond "use distinct `--state` filenames."
- Requires a coordinator agent in the loop; there's no headless/unattended entry point that
  doesn't involve an LLM driving the browser (see "How it works" for why that's
  deliberate).
- Assumes the ChatGPT GitHub connector's access scope already covers (or will be granted
  access to) whatever repository the request targets.
- ChatGPT's UI isn't pinned to specific selectors, so behavior can shift slightly across
  ChatGPT UI versions — the coordinator agent adapts each round rather than relying on a
  brittle fixed script.

## Contributing

Standard PR flow — branch protection and required checks gate `main`. If you're changing
`pr_cycle.py`, add tests against the existing fake-runner pattern before wiring up new
behavior; TDD is how this project has been built so far.

## License

MIT — see [LICENSE](LICENSE).
