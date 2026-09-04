#!/usr/bin/env python3
"""Minimal least-privilege MCP server for WebGPT Orchestrator's Forge Loop.

This server intentionally exposes PR metadata, allowlisted test profiles, and handoff text;
it does not expose a generic shell, filesystem, or merge tool.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from forge_loop import PRODUCT_ID, _comment_markdown, load_commands, resolve_pr, run_test_cycle


TOOL_DEFINITIONS = [
    {
        "name": "webgpt_pr_status",
        "description": "Read immutable GitHub pull request metadata.",
        "inputSchema": {"type": "object", "required": ["pr"], "properties": {"pr": {"type": "string"}}},
    },
    {
        "name": "webgpt_pr_test",
        "description": "Run one registered test profile against the immutable PR head in isolation.",
        "inputSchema": {
            "type": "object",
            "required": ["pr"],
            "properties": {
                "pr": {"type": "string"},
                "profile": {"type": "string", "enum": ["smoke", "default"]},
            },
        },
    },
    {
        "name": "webgpt_pr_handoff",
        "description": "Format Codex test evidence for the WebGPT implementation worker.",
        "inputSchema": {
            "type": "object",
            "required": ["report"],
            "properties": {"report": {"type": "object"}},
        },
    },
]


def load_profile_commands(profile: str, plugin_dir: Path) -> list[list[str]]:
    if profile not in {"smoke", "default"}:
        raise ValueError(f"unknown command profile: {profile}")
    filename = "commands.smoke.json" if profile == "smoke" else "commands.example.json"
    return load_commands(plugin_dir / filename)


def _text_result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}]}


def dispatch_request(request: dict[str, Any], *, plugin_dir: Path | None = None) -> dict[str, Any]:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": PRODUCT_ID, "version": "0.1.0"},
        }
    elif method == "notifications/initialized":
        return {}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOL_DEFINITIONS}
    elif method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "webgpt_pr_status":
            repository, number = _parse_pr(arguments)
            result = _text_result(resolve_pr(repository, number))
        elif name == "webgpt_pr_test":
            pr = arguments.get("pr")
            if not isinstance(pr, str):
                raise ValueError("webgpt_pr_test requires a PR reference")
            profile = arguments.get("profile", "smoke")
            if not isinstance(profile, str):
                raise ValueError("profile must be a string")
            commands = load_profile_commands(profile, plugin_dir or Path(__file__).parent.parent)
            result = _text_result(run_test_cycle(pr, commands))
        elif name == "webgpt_pr_handoff":
            report = arguments.get("report")
            if not isinstance(report, dict):
                raise ValueError("webgpt_pr_handoff requires a report object")
            result = _text_result({"handoff": _comment_markdown(report)})
        else:
            raise ValueError(f"unknown tool: {name}")
    else:
        raise ValueError(f"unsupported MCP method: {method}")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _parse_pr(arguments: dict[str, Any]) -> tuple[str, int]:
    from forge_loop import parse_pr_reference

    pr = arguments.get("pr")
    if not isinstance(pr, str):
        raise ValueError("PR reference is required")
    return parse_pr_reference(pr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-dir", type=Path, default=Path(__file__).parent.parent)
    args = parser.parse_args()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = dispatch_request(request, plugin_dir=args.plugin_dir)
        except (ValueError, OSError, RuntimeError) as error:
            response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None, "error": {"code": -32602, "message": str(error)}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
