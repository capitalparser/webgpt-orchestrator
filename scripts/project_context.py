#!/usr/bin/env python3
"""Local repository-to-ChatGPT routing records; browser verification stays with the agent."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from forge_loop import _write_cycle_state, parse_pr_reference, redact_text


def repository_key(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError("expected GitHub owner/repo")
    return value.lower()


def project_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("project URL must be a string")
    parsed = urlparse(value)
    match = re.fullmatch(r"/g/(g-p-[A-Za-z0-9-]+)/project/?", parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "chatgpt.com" or not match or parsed.query or parsed.fragment:
        raise ValueError("expected a browser-observed ChatGPT project home URL")
    return match[1]


def read_mapping(registry: Path, repository: str) -> dict | None:
    path = registry / f"{repository}.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("repository") != repository:
        raise ValueError("invalid repository project mapping")
    if not isinstance(value.get("project_url"), str) or not isinstance(value.get("project_name"), str) or not value["project_name"].strip():
        raise ValueError("invalid project URL or name")
    project_id(value["project_url"])
    return {key: value[key] for key in ("repository", "project_url", "project_name")}


def bind_mapping(registry: Path, mapping: dict) -> dict:
    """Publish a complete binding without overwriting another task's binding."""
    path = registry / f"{mapping['repository']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(mapping, stream)
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = read_mapping(registry, mapping["repository"])
            if existing is None or project_id(existing["project_url"]) != project_id(mapping["project_url"]):
                raise ValueError("repository already linked to another project; resolve the mapping explicitly")
            return existing
        return read_mapping(registry, mapping["repository"])
    finally:
        temporary.unlink()


def attach_mapping(state_path: Path, mapping: dict, conversation_url: str | None) -> dict:
    state = json.loads(state_path.read_text())
    if not isinstance(state, dict):
        raise ValueError("task state must be a JSON object")
    for key in ("repository", "project_url", "conversation_url"):
        if state.get(key) is not None and (not isinstance(state[key], str) or not state[key]):
            raise ValueError(f"invalid task {key}")
    if state.get("repository") and repository_key(state["repository"]) != mapping["repository"]:
        raise ValueError("task belongs to a different repository")
    if state.get("pr_ref"):
        if not isinstance(state["pr_ref"], str) or parse_pr_reference(state["pr_ref"])[0].lower() != mapping["repository"]:
            raise ValueError("task PR belongs to a different repository")
    if state.get("project_url") and project_id(state["project_url"]) != project_id(mapping["project_url"]):
        raise ValueError("task belongs to a different project")
    conversation = conversation_url or state.get("conversation_url")
    if conversation:
        parsed = urlparse(conversation)
        match = re.fullmatch(r"/(?:g/(g-p-[A-Za-z0-9-]+)/)?c/[A-Za-z0-9-]+/?", parsed.path)
        if parsed.scheme != "https" or parsed.netloc != "chatgpt.com" or not match or parsed.query or parsed.fragment:
            raise ValueError("expected a browser-observed ChatGPT conversation URL")
        if match[1] and match[1] != project_id(mapping["project_url"]):
            raise ValueError("conversation belongs to a different project")
        if state.get("conversation_url") and state["conversation_url"] != conversation:
            raise ValueError("task already has a conversation; use a new task state for a handoff")
    state.update(mapping)
    if conversation:
        state["conversation_url"] = conversation
    _write_cycle_state(state_path, state)
    return json.loads(state_path.read_text())


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("lookup", "bind", "attach"):
        command = commands.add_parser(action)
        command.add_argument("--repository", required=True)
        command.add_argument("--registry", type=Path, default=Path.home() / ".local/share/webgpt-orchestrator/projects")
        if action == "bind":
            command.add_argument("--project-url", required=True)
            command.add_argument("--project-name", required=True)
        if action == "attach":
            command.add_argument("--state", type=Path, required=True)
            command.add_argument("--conversation-url")
    args = parser.parse_args()
    try:
        repository = repository_key(args.repository)
        mapping = read_mapping(args.registry, repository)
        if args.action == "bind":
            project_id(args.project_url)
            if not args.project_name.strip():
                raise ValueError("project name must not be empty")
            mapping = {"repository": repository, "project_url": args.project_url, "project_name": args.project_name}
            mapping = bind_mapping(args.registry, mapping)
        if args.action == "attach":
            if mapping is None:
                raise ValueError("link the repository project before attaching task state")
            attach_mapping(args.state, mapping, args.conversation_url)
        result = {"status": "PROJECT_LINKED", **mapping} if mapping else {
            "status": "PROJECT_NOT_LINKED", "repository": repository,
            "next_action": "Search existing ChatGPT projects before creating one; verify identity in the browser.",
        }
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "BLOCKED_PROJECT_UNAVAILABLE", "error": redact_text(str(error))}))
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
