#!/usr/bin/env python3
"""Forge Loop runner for safe PR orchestration and isolated verification."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlparse


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SECRET_RE = re.compile(r"(?i)(?:ghp_|github_pat_|sk-[A-Za-z0-9_-]+|bearer\s+)[A-Za-z0-9_./+=:-]+")
PATH_RE = re.compile(r"(?:(?:/Users|/home|/private/var|[A-Za-z]:\\)[^\s'\"]+)")
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
PRODUCT_ID = "webgpt-orchestrator"
WORKFLOW_NAME = "Forge Loop"


def parse_pr_reference(value: str) -> tuple[str, int]:
    """Parse a GitHub pull URL or owner/repository#number reference."""
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        if parsed.netloc != "github.com":
            raise ValueError("pull request must be a github.com pull request")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 4 or parts[2] != "pull" or not parts[3].isdigit():
            raise ValueError("expected a GitHub pull request URL")
        repository = f"{parts[0]}/{parts[1]}"
        number = int(parts[3])
    else:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#([0-9]+)", value)
        if not match:
            raise ValueError("expected a GitHub pull request URL or owner/repository#number")
        repository, number_text = match.groups()
        number = int(number_text)
    if not REPOSITORY_RE.fullmatch(repository) or number < 1:
        raise ValueError("invalid pull request repository or number")
    return repository, number


def redact_text(value: str) -> str:
    """Remove credentials and machine-specific absolute paths from user-visible output."""
    redacted = SECRET_RE.sub("[REDACTED]", value)
    return PATH_RE.sub("[LOCAL_PATH]", redacted)


def build_report(
    *,
    repository: str,
    pull_request: int,
    head_sha: str,
    commands: list[tuple[list[str], int, float]],
    status: str,
    comment_markdown: str,
) -> dict[str, object]:
    if status not in {"TEST_PASSED", "TEST_FAILED", "BLOCKED"}:
        raise ValueError("invalid report status")
    if not SHA_RE.fullmatch(head_sha):
        raise ValueError("head_sha must be a 40-character commit SHA")
    return {
        "status": status,
        "repository": repository,
        "pull_request": pull_request,
        "head_sha": head_sha,
        "commands": [
            {
                "argv": argv,
                "exit_code": exit_code,
                "duration_seconds": round(duration, 3),
            }
            for argv, exit_code, duration in commands
        ],
        "comment_markdown": redact_text(comment_markdown),
    }


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 900,
    runner: CommandRunner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    if not argv or any(not isinstance(part, str) or not part for part in argv):
        raise ValueError("commands must be non-empty argv arrays")
    return runner(
        list(argv),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def resolve_pr(repository: str, number: int, runner: CommandRunner = subprocess.run) -> dict[str, object]:
    result = _run(
        ["gh", "api", f"repos/{repository}/pulls/{number}"],
        runner=runner,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(redact_text(result.stdout or "gh could not resolve the pull request"))
    try:
        payload = json.loads(result.stdout)
        head = payload["head"]
        head_repo = head["repo"]["full_name"]
        sha = head["sha"]
        ref = head["ref"]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("GitHub returned invalid pull request metadata") from error
    if not REPOSITORY_RE.fullmatch(head_repo) or not isinstance(ref, str) or not SHA_RE.fullmatch(sha):
        raise RuntimeError("GitHub returned unsafe pull request metadata")
    return {"repository": repository, "number": number, "head_repository": head_repo, "head_ref": ref, "head_sha": sha}


def create_repo(
    name: str,
    *,
    public: bool = False,
    runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    """Create a new GitHub repository via gh and verify it exists."""
    if not REPO_NAME_RE.fullmatch(name):
        raise ValueError(
            "repository name must start with a letter or digit and contain only letters, digits, '-', '_', or '.'"
        )
    visibility_flag = "--public" if public else "--private"
    created = _run(["gh", "repo", "create", name, visibility_flag], runner=runner, timeout=60)
    if created.returncode != 0:
        raise RuntimeError(redact_text(created.stdout or "gh could not create the repository"))
    match = re.search(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", created.stdout or "")
    if not match:
        raise RuntimeError("gh repo create did not return a repository URL")
    full_name = match.group(1)
    verify = _run(["gh", "api", f"repos/{full_name}"], runner=runner, timeout=60)
    if verify.returncode != 0:
        raise RuntimeError(redact_text(verify.stdout or "created repository could not be verified"))
    try:
        payload = json.loads(verify.stdout)
        verified_full_name = payload["full_name"]
        html_url = payload["html_url"]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("GitHub returned invalid repository metadata") from error
    if not REPOSITORY_RE.fullmatch(verified_full_name):
        raise RuntimeError("GitHub returned an unsafe repository name")
    if not isinstance(html_url, str) or not html_url.startswith("https://github.com/"):
        raise RuntimeError("GitHub returned an unsafe repository URL")
    return {"repository": verified_full_name, "html_url": html_url}


def _comment(repository: str, number: int, body: str, runner: CommandRunner) -> None:
    result = _run(
        ["gh", "api", f"repos/{repository}/issues/{number}/comments", "-f", f"body={redact_text(body)}"],
        runner=runner,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(redact_text(result.stdout or "GitHub comment failed"))


def _comment_markdown(report: dict[str, object]) -> str:
    lines = [
        "<!-- webgpt-orchestrator:forge-loop -->",
        f"## {WORKFLOW_NAME} PR test: {report['status']}",
        f"- Repository: `{report['repository']}`",
        f"- PR: `#{report['pull_request']}`",
        f"- Head SHA: `{report['head_sha']}`",
        "",
        "| Command | Exit | Duration |",
        "|---|---:|---:|",
    ]
    for command in report["commands"]:
        argv = " ".join(f"`{part}`" for part in command["argv"])
        lines.append(f"| {argv} | {command['exit_code']} | {command['duration_seconds']}s |")
    failure_output = report.get("failure_output")
    if failure_output:
        lines.append("")
        lines.append("Failure output (redacted):")
        lines.append("```")
        lines.append(str(failure_output))
        lines.append("```")
    return "\n".join(lines)


def load_commands(path: Path) -> list[list[str]]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read command configuration: {path.name}") from error
    if not isinstance(value, list) or not value or any(
        not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command)
        for command in value
    ):
        raise ValueError("commands JSON must be a non-empty array of non-empty argv arrays")
    return value


def run_test_cycle(
    pr_ref: str,
    commands: list[list[str]],
    *,
    post_comment: bool = False,
    runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    repository, number = parse_pr_reference(pr_ref)
    metadata = resolve_pr(repository, number, runner)
    executed: list[tuple[list[str], int, float]] = []
    with tempfile.TemporaryDirectory(prefix=f"{PRODUCT_ID}-") as directory:
        checkout = Path(directory) / "checkout"
        clone = _run(["git", "clone", "--no-checkout", f"https://github.com/{metadata['head_repository']}.git", str(checkout)], runner=runner, timeout=900)
        if clone.returncode != 0:
            raise RuntimeError("isolated PR checkout failed: " + redact_text(clone.stdout or ""))
        detached = _run(["git", "checkout", "--detach", str(metadata["head_sha"])], cwd=checkout, runner=runner, timeout=120)
        if detached.returncode != 0:
            raise RuntimeError("PR head SHA checkout failed: " + redact_text(detached.stdout or ""))
        for argv in commands:
            started = time.monotonic()
            result = _run(argv, cwd=checkout, runner=runner)
            duration = time.monotonic() - started
            executed.append((argv, result.returncode, duration))
            if result.returncode != 0:
                output = redact_text((result.stdout or "").strip())[-2000:]
                break
        status = "TEST_PASSED" if executed and all(code == 0 for _, code, _ in executed) else "TEST_FAILED"
        report = build_report(
            repository=repository,
            pull_request=number,
            head_sha=str(metadata["head_sha"]),
            commands=executed,
            status=status,
            comment_markdown="",
        )
        if executed and executed[-1][1] != 0 and output:
            report["failure_output"] = output
        report["comment_markdown"] = _comment_markdown(report)
    if post_comment:
        _comment(repository, number, str(report["comment_markdown"]), runner)
        report["comment_published"] = True
    else:
        report["comment_published"] = False
    return report


def write_result(path: Path, report: dict[str, object]) -> None:
    """Persist only the already-redacted result for a later handoff."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(path)


def _write_cycle_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)


def derive_task_space(state_path: Path) -> str:
    """Derive a stable ego-browser task space name from the state file path."""
    return f"{PRODUCT_ID}:{state_path.stem}"


def normalize_intent_brief(intent_brief: str | None) -> str:
    """Return a non-empty user-confirmed intent brief for the first WebGPT handoff."""
    if not isinstance(intent_brief, str) or not (normalized := intent_brief.strip()):
        raise ValueError("Forge Loop intent brief must not be empty")
    return normalized


def normalize_webgpt_question(webgpt_question: str | None) -> str:
    """Return a non-empty question confirmed in the coordinator's CLI conversation."""
    if not isinstance(webgpt_question, str) or not (normalized := webgpt_question.strip()):
        raise ValueError("Forge Loop WebGPT question must not be empty")
    return normalized


def start_cycle(
    request: str,
    state_path: Path,
    *,
    intent_brief: str | None = None,
    webgpt_question: str | None = None,
    max_iterations: int = 5,
    new_repo: str | None = None,
    public: bool = False,
    runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    """Start a conversation handoff and persist its state for later PR testing."""
    request = request.strip()
    if not request:
        raise ValueError("Forge Loop request must not be empty")
    intent_brief = normalize_intent_brief(intent_brief)
    webgpt_question = normalize_webgpt_question(webgpt_question)
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    repository_notice = ""
    if new_repo:
        created = create_repo(new_repo, public=public, runner=runner)
        repository_notice = (
            f"Use the existing GitHub repository {created['repository']} "
            f"({created['html_url']}) for this work. Do not create a new repository.\n\n"
        )
    state = {
        "status": "AWAITING_WEBGPT_PR",
        "request": request,
        "intent_brief": intent_brief,
        "webgpt_question": webgpt_question,
        "pr_ref": None,
        "iteration": 0,
        "max_iterations": max_iterations,
        "task_space": derive_task_space(state_path),
        "next_action": "Send the confirmed WebGPT question and user intent, then return a PR URL.",
        "webgpt_prompt": (
            repository_notice
            + "Follow the confirmed WebGPT question and user intent below through your existing GitHub connector, "
            "open a PR, and return the exact PR URL and head SHA. Do not merge it.\n\n"
            "## Confirmed WebGPT question\n"
            + webgpt_question
            + "\n\n## Confirmed user intent\n"
            + intent_brief
            + "\n\n## Implementation request\n"
            + request
            + "\n\nReply with the pull request URL on its own trailing line as `PR_URL: <url>`."
        ),
    }
    _write_cycle_state(state_path, state)
    return state


def resume_cycle(
    state_path: Path,
    pr_ref: str,
    commands: list[list[str]],
    *,
    post_comment: bool = False,
    runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    """Test the supplied PR and advance the persisted WebGPT conversation state."""
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read Forge Loop state: {state_path}") from error
    if not isinstance(state, dict) or not isinstance(state.get("request"), str):
        raise ValueError("Forge Loop state is invalid")
    max_iterations = int(state.get("max_iterations", 5))
    current_iteration = int(state.get("iteration", 0))
    if current_iteration >= max_iterations:
        result = {
            **state,
            "status": "BLOCKED_MAX_ITERATIONS",
            "pr_ref": pr_ref,
            "next_action": "Stop: max_iterations reached. Report to the user.",
        }
        _write_cycle_state(state_path, result)
        return result
    report = run_test_cycle(pr_ref, commands, post_comment=post_comment, runner=runner)
    passed = report["status"] == "TEST_PASSED"
    iteration = current_iteration + 1
    next_action = (
        "Review branch protection and merge the PR."
        if passed
        else "Send the failure handoff to WebGPT and ask it to update the PR."
    )
    result = {
        **state,
        "status": "READY_TO_MERGE" if passed else "AWAITING_WEBGPT_FIX",
        "pr_ref": pr_ref,
        "iteration": iteration,
        "report": report,
        "next_action": next_action,
        "webgpt_handoff": (
            report["comment_markdown"]
            + "\n\nPush a fix, then reply with the pull request URL on its own trailing line "
            "as `PR_URL: <url>`."
            if not passed
            else ""
        ),
    }
    _write_cycle_state(state_path, result)
    return result


def validate_cycle_arguments(
    *,
    request: str | None,
    intent_brief: str | None,
    pr: str | None,
    new_repo: str | None,
    public: bool,
    webgpt_question: str | None = None,
) -> None:
    if request and pr:
        raise ValueError("forge accepts either --request or --pr, not both")
    if request and not intent_brief:
        raise ValueError("forge --request requires --intent-brief after user confirmation")
    if request and not webgpt_question:
        raise ValueError("forge --request requires --webgpt-question before WebGPT handoff")
    if intent_brief and not request:
        raise ValueError("--intent-brief is only valid with --request")
    if webgpt_question and not request:
        raise ValueError("--webgpt-question is only valid with --request")
    if pr and (new_repo or public):
        raise ValueError("--new-repo/--public are only valid with --request")
    if not request and not pr:
        raise ValueError("forge requires --request or --pr")


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "test"):
        command = subparsers.add_parser(action)
        command.add_argument("--pr", required=True)
        if action == "test":
            command.add_argument("--commands-json", type=Path, required=True)
            command.add_argument("--post-comment", action="store_true")
            command.add_argument("--result", type=Path)
    for action in ("handoff", "iterate"):
        handoff = subparsers.add_parser(action)
        handoff.add_argument("--result", type=Path, required=True)
    forge = subparsers.add_parser(
        "forge",
        aliases=["cycle"],
        help="run or continue the Forge Loop",
    )
    forge.add_argument("--state", type=Path, required=True)
    forge.add_argument("--request")
    forge.add_argument("--intent-brief")
    forge.add_argument("--webgpt-question")
    forge.add_argument("--pr")
    forge.add_argument("--commands-json", type=Path)
    forge.add_argument("--post-comment", action="store_true")
    forge.add_argument("--new-repo")
    forge.add_argument("--public", action="store_true")
    forge.add_argument("--max-iterations", type=int, default=5)
    args = parser.parse_args()
    try:
        if args.action == "status":
            repository, number = parse_pr_reference(args.pr)
            print(json.dumps(resolve_pr(repository, number), indent=2))
        elif args.action == "test":
            report = run_test_cycle(args.pr, load_commands(args.commands_json), post_comment=args.post_comment)
            if args.result:
                write_result(args.result, report)
            print(json.dumps(report, indent=2))
            return 0 if report["status"] == "TEST_PASSED" else 1
        elif args.action in {"handoff", "iterate"}:
            report = json.loads(args.result.read_text())
            print(f"WebGPT handoff: {report.get('status', 'BLOCKED')} for {report.get('repository', '?')}#{report.get('pull_request', '?')} at {report.get('head_sha', '?')}")
            print(report.get("comment_markdown", "No report comment available."))
        else:
            validate_cycle_arguments(
                request=args.request,
                intent_brief=args.intent_brief,
                pr=args.pr,
                new_repo=args.new_repo,
                public=args.public,
                webgpt_question=args.webgpt_question,
            )
            if args.request:
                result = start_cycle(
                    args.request,
                    args.state,
                    intent_brief=args.intent_brief,
                    webgpt_question=args.webgpt_question,
                    max_iterations=args.max_iterations,
                    new_repo=args.new_repo,
                    public=args.public,
                )
            else:
                if not args.commands_json:
                    raise ValueError("forge --pr requires --commands-json")
                result = resume_cycle(
                    args.state,
                    args.pr,
                    load_commands(args.commands_json),
                    post_comment=args.post_comment,
                )
            print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"status": "BLOCKED", "error": redact_text(str(error))}, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
