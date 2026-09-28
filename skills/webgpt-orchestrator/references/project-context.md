# Project setup and sources

Use this procedure at the first repository-scoped WebGPT invocation, then recheck the
binding on subsequent tasks. The coordinator uses ego-browser for the visible UI and the
local CLI for durable routing records. The CLI does not create Projects or verify the UI.
Run commands from the orchestrator installation root (`SKILL.md` is under `skills/`).

## 1. Discover and connect before starting a chat

1. Resolve the intended GitHub `owner/repo` from the user's task or PR. For work on the
   current repository, inspect its Git remote; worktree and branch names are not repository
   identities. If `--new-repo` is requested, create the GitHub repository first and use its
   returned identity. Resolve ambiguity in the coordinator conversation.
2. Look up the local record:
   `python3 scripts/project_context.py lookup --repository <owner/repo>`.
   Records are shared by Codex and Claude at
   `~/.local/share/webgpt-orchestrator/projects/<owner>/<repo>.json`.
3. Open the mapped Project in the task's ego-browser space and verify the signed-in
   account/workspace and intended Project. If `PROJECT_NOT_LINKED`, search the account's
   existing Projects, including descriptive names and relevant repository references in
   their instructions or sources. The sidebar's pinned subset is not an exhaustive list.
   Reuse a user-specified Project or an unambiguous existing match, keeping its name.
4. Only after a complete search establishes no match, create a Project named `owner/repo`
   for the authorized task. Inspect the UI rather than inventing selectors or Project IDs.
   If search is incomplete, matches are ambiguous, or creation/access is unavailable,
   report `BLOCKED_PROJECT_UNAVAILABLE` with the cause. Do not assume absence or use a
   generic chat. Do not change sharing settings or delete a Project to make room.
5. Save the actual Project home URL and name after verifying its identity:

   ```text
   python3 scripts/project_context.py bind --repository <owner/repo> --project-url <observed-project-home-url> --project-name "<existing or new name>"
   ```

   `bind` is idempotent for the same Project and refuses to overwrite a different binding.
   A stale mapping requires explicit resolution; it is not permission to create another
   Project. The helper accepts observed `https://chatgpt.com/g/g-p-…/project` home URLs.
   If the product changes its URL format, update and test the parser rather than fabricating
   an accepted URL. Records are not account credentials; recheck account/workspace every time.

Serialize first-time search/create/bind for the same repository across agents. The CLI
prevents conflicting file replacement but cannot lock a browser-side Project creation.
Concurrent tasks may share a Project once linked; keep separate state files and task spaces.
Multiple repositories may use one Project when the user explicitly selects that arrangement.

## 2. Attach each task and keep its conversation

After the question/intent preflight, create the Forge state normally, then attach the mapping:

```text
python3 scripts/project_context.py attach --repository <owner/repo> --state <state.json>
```

For planning, research, or source management, use a separate local JSON task record instead
of Forge state (initialize it with the task description). `attach` requires an existing JSON
object and preserves its other fields. It records `repository`, `project_url`, and
`project_name`; Forge resumes preserve those fields and reject a PR from another repository.

For a new task, open the Project's new-chat control. For a resumed task, reopen its recorded
conversation. Before sending, verify Project membership in the UI along with Chat, the
required model, and the connector. Once the conversation URL exists, save it:

```text
python3 scripts/project_context.py attach --repository <owner/repo> --state <state.json> --conversation-url <observed-conversation-url>
```

The CLI rejects another Project's URL and replacement of an already recorded conversation.
A plain `/c/…` URL can be recorded only after the coordinator verifies membership in the UI;
its URL alone cannot establish membership. On legacy tasks, recover the correct chat from
the task space/history and move it into the Project if supported, then verify. Do not
silently substitute a new chat when recovery fails.

If a long conversation becomes confused or the scope changes substantially, write a short
handoff with goal, accepted decisions, current PR/SHA, completed/remaining work, exact test
results, uncertainties, and original source links. Start a new task state and conversation
inside the same Project, recording the predecessor state/URL. Do not reset an active Forge
iteration cap through a context handoff: carry forward its iteration, max_iterations, and
confirmed intent. Past chats provide context, not fresh authorization or current GitHub facts.

## 3. Maintain useful sources

On project setup, inspect existing instructions and Sources. Preserve them; add only
material relevant to the authorized work. Before a task, check that its required sources
are present and current. At completion, preserve accepted decisions and useful handoff
material for later tasks. Do not upload an entire repository by default.

- Use Sources for persistent reference documents, approved decisions, and design images;
  use individual chat attachments for material needed only in that task. Attach a short
  note to visual references explaining which features to follow and which version they replace.
- Use the current ego-browser upload/file-chooser APIs for local files. Observe the Sources
  list after uploading and verify completion and the expected item. A successful click or
  dispatched file selection is not proof that upload/processing succeeded.
- Keep a coordinator-maintained `sources.json` beside the repository mapping, under
  `<owner>/<repo>/sources.json`. For each item record its original path/URL, purpose,
  version or SHA-256 for local files, observed Project source label/ID, last verified time,
  and status (`pending`, `verified`, `failed`, or `superseded`). Keep original files outside
  ChatGPT. This manifest is maintained by the agent; the routing CLI does not sync sources.
- Compare the manifest with the actual source list before uploading. Same filename does
  not imply replacement or identical content. Skip verified unchanged items; upload a
  changed item with a distinguishable version, verify it, then record the predecessor as
  superseded. Remove a remote predecessor only when its removal is within the authorized
  scope and no other task depends on it. Do not erase user-managed sources automatically.
- For web references, preserve the original URL, retrieval date, and relevant excerpt in
  a reference document or pasted text. Use an app link only when the UI supports that app.
  A saved URL or connected app does not by itself prove content is fetched or synchronized.
- Select necessary sources explicitly in the task prompt and request references to the
  materials actually used. Project storage does not guarantee that every source or past
  conversation is included in every model response.

## Limits and failure handling

As checked on 2026-09-28, the official guidance allows unlimited Projects. File limits are
5 per Project for Free, 25 for Go/Plus, and 40 for Pro/Edu/Business/Enterprise, with at most
10 files per upload batch. Recheck the current account's UI and official guidance when a
limit matters; do not treat these figures as permanent or as a guarantee of available quota.
Workspace permissions can restrict Project creation independently of plan limits.

Before adding sources, inspect existing usage. If quota, permissions, or a file error
blocks a required source, report `BLOCKED_PROJECT_SOURCES` and identify the unresolved item.
Keep the Project mapping and original files. Do not create extra Projects to bypass a
source limit or silently continue as though the source was available. Prefer concise,
source-linked reference documents and current versions to accumulating redundant uploads.

Sources: [Projects in ChatGPT](https://help.openai.com/en/articles/10169521-projects-in-chatgpt)
and [Projects and chats](https://learn.chatgpt.com/docs/projects).
