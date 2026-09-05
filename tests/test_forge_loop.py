import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from forge_loop import (
    _main,
    _comment_markdown,
    build_report,
    create_repo,
    parse_pr_reference,
    redact_text,
    run_test_cycle,
    start_cycle,
    resume_cycle,
    validate_cycle_arguments,
)
from mcp_server import dispatch_request, load_profile_commands


INTENT_BRIEF = (
    "Goal: add dark mode.\n"
    "Success: preferences persist across reloads.\n"
    "Scope: existing web app only.\n"
    "Constraints: keep accessibility contrast.\n"
    "Validation: run the frontend test suite."
)


def test_parse_pr_url_returns_repository_and_number():
    assert parse_pr_reference("https://github.com/acme/widget/pull/42") == ("acme/widget", 42)


def test_parse_pr_reference_rejects_non_pull_url():
    with pytest.raises(ValueError, match="pull request"):
        parse_pr_reference("https://github.com/acme/widget/issues/42")


def test_redact_text_removes_secrets_and_absolute_paths():
    value = "token ghp_abcdefghijklmnopqrstuvwxyz1234567890 /Users/kjun/private/file.txt"
    result = redact_text(value)
    assert "ghp_" not in result
    assert "/Users/" not in result
    assert "[REDACTED]" in result


def test_build_report_has_stable_status_and_command_shape():
    report = build_report(
        repository="acme/widget",
        pull_request=42,
        head_sha="a" * 40,
        commands=[(["pytest", "-q"], 0, 0.25)],
        status="TEST_PASSED",
        comment_markdown="All checks passed.",
    )
    assert report == {
        "status": "TEST_PASSED",
        "repository": "acme/widget",
        "pull_request": 42,
        "head_sha": "a" * 40,
        "commands": [{"argv": ["pytest", "-q"], "exit_code": 0, "duration_seconds": 0.25}],
        "comment_markdown": "All checks passed.",
    }


def test_run_test_cycle_checks_out_immutable_head_and_runs_argv_without_shell():
    calls = []
    sha = "b" * 40

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[:3] == ["gh", "api", "repos/acme/widget/pulls/42"]:
            payload = {"head": {"repo": {"full_name": "acme/widget"}, "ref": "feature", "sha": sha}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload))
        return subprocess.CompletedProcess(argv, 0, "")

    report = run_test_cycle("https://github.com/acme/widget/pull/42", [["pytest", "-q"]], runner=fake_runner)

    assert report["status"] == "TEST_PASSED"
    assert [call[0] for call in calls][-2:] == [
        ["git", "checkout", "--detach", sha],
        ["pytest", "-q"],
    ]
    assert all(call[1].get("shell") is not True for call in calls)


def test_run_test_cycle_reports_failure_and_only_comments_when_explicit():
    calls = []
    sha = "c" * 40

    def fake_runner(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["gh", "api"] and "pulls/7" in argv[2]:
            payload = {"head": {"repo": {"full_name": "acme/widget"}, "ref": "feature", "sha": sha}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload))
        if argv[0] == "pytest":
            return subprocess.CompletedProcess(argv, 1, "assert failed")
        return subprocess.CompletedProcess(argv, 0, "")

    report = run_test_cycle("acme/widget#7", [["pytest", "-q"]], runner=fake_runner)
    assert report["status"] == "TEST_FAILED"
    assert not any("comments" in part for call in calls for part in call)

    report = run_test_cycle("acme/widget#7", [["pytest", "-q"]], post_comment=True, runner=fake_runner)
    assert report["comment_published"] is True
    assert any("comments" in part for call in calls for part in call)


def test_start_cycle_creates_webgpt_handoff_state(tmp_path):
    state_path = tmp_path / "cycle.json"

    result = start_cycle("Add dark mode", state_path, intent_brief=INTENT_BRIEF)

    assert result["status"] == "AWAITING_WEBGPT_PR"
    assert result["request"] == "Add dark mode"
    assert result["next_action"] == "Send the confirmed user intent to WebGPT and return a PR URL."
    assert json.loads(state_path.read_text())["iteration"] == 0


def test_start_cycle_includes_confirmed_intent_brief_in_webgpt_handoff(tmp_path):
    state_path = tmp_path / "cycle.json"
    intent_brief = (
        "Goal: add dark mode.\n"
        "Success: preferences persist across reloads.\n"
        "Scope: existing web app only.\n"
        "Constraints: keep accessibility contrast.\n"
        "Validation: run the frontend test suite."
    )

    result = start_cycle("Add dark mode", state_path, intent_brief=intent_brief)

    assert result["intent_brief"] == intent_brief
    assert "## Confirmed user intent\n" + intent_brief in result["webgpt_prompt"]
    assert json.loads(state_path.read_text())["intent_brief"] == intent_brief


def test_resume_cycle_updates_state_and_returns_merge_gate(tmp_path):
    state_path = tmp_path / "cycle.json"
    start_cycle("Add dark mode", state_path, intent_brief=INTENT_BRIEF)
    sha = "d" * 40

    def fake_runner(argv, **kwargs):
        if argv[:3] == ["gh", "api", "repos/acme/widget/pulls/42"]:
            payload = {"head": {"repo": {"full_name": "acme/widget"}, "ref": "feature", "sha": sha}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload))
        return subprocess.CompletedProcess(argv, 0, "")

    result = resume_cycle(
        state_path,
        "acme/widget#42",
        [["pytest", "-q"]],
        runner=fake_runner,
    )

    assert result["status"] == "READY_TO_MERGE"
    assert result["report"]["status"] == "TEST_PASSED"
    assert result["next_action"] == "Review branch protection and merge the PR."
    assert json.loads(state_path.read_text())["iteration"] == 1


def test_mcp_tools_expose_only_pr_cycle_operations():
    result = dispatch_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in result["result"]["tools"]}
    assert names == {"webgpt_pr_status", "webgpt_pr_test", "webgpt_pr_handoff"}
    assert "exec" not in names


def test_mcp_initialize_uses_orchestrator_product_identity():
    result = dispatch_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert result["result"]["serverInfo"]["name"] == "webgpt-orchestrator"


def test_mcp_rejects_unregistered_command_profile(tmp_path):
    with pytest.raises(ValueError, match="unknown command profile"):
        load_profile_commands("arbitrary-shell", tmp_path)


def test_cli_help_exposes_forge_as_the_canonical_loop_command(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["forge_loop.py", "--help"])

    with pytest.raises(SystemExit) as exit_info:
        _main()

    assert exit_info.value.code == 0
    assert "{status,test,handoff,iterate,forge,cycle}" in capsys.readouterr().out


def test_legacy_pr_cycle_entrypoint_still_runs_cycle_alias(tmp_path):
    state_path = tmp_path / "legacy-cycle.json"
    entrypoint = Path(__file__).parents[1] / "scripts" / "pr_cycle.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(entrypoint),
            "cycle",
            "--request",
            "Add dark mode",
            "--intent-brief",
            INTENT_BRIEF,
            "--state",
            str(state_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "AWAITING_WEBGPT_PR"
    assert json.loads(state_path.read_text())["request"] == "Add dark mode"


def test_forge_request_requires_confirmed_intent_brief(tmp_path):
    entrypoint = Path(__file__).parents[1] / "scripts" / "forge_loop.py"
    state_path = tmp_path / "cycle.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(entrypoint),
            "forge",
            "--request",
            "Add dark mode",
            "--state",
            str(state_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "--intent-brief" in json.loads(completed.stdout)["error"]


def test_start_cycle_rejects_an_empty_intent_brief(tmp_path):
    with pytest.raises(ValueError, match="intent brief"):
        start_cycle("Add dark mode", tmp_path / "cycle.json", intent_brief="   ")


def test_start_cycle_derives_task_space_and_default_max_iterations(tmp_path):
    state_path = tmp_path / "add-dark-mode.json"

    result = start_cycle("Add dark mode", state_path, intent_brief=INTENT_BRIEF)

    assert result["task_space"] == "webgpt-orchestrator:add-dark-mode"
    assert result["max_iterations"] == 5


def test_start_cycle_accepts_custom_max_iterations(tmp_path):
    state_path = tmp_path / "cycle.json"

    result = start_cycle("Add dark mode", state_path, intent_brief=INTENT_BRIEF, max_iterations=2)

    assert result["max_iterations"] == 2


def test_start_cycle_rejects_non_positive_max_iterations(tmp_path):
    with pytest.raises(ValueError, match="max_iterations"):
        start_cycle("Add dark mode", tmp_path / "cycle.json", intent_brief=INTENT_BRIEF, max_iterations=0)


def test_resume_cycle_blocks_once_max_iterations_reached(tmp_path):
    state_path = tmp_path / "cycle.json"
    start_cycle("Add dark mode", state_path, intent_brief=INTENT_BRIEF, max_iterations=1)
    sha = "e" * 40

    def fake_runner(argv, **kwargs):
        if argv[:3] == ["gh", "api", "repos/acme/widget/pulls/42"]:
            payload = {"head": {"repo": {"full_name": "acme/widget"}, "ref": "feature", "sha": sha}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload))
        if argv[0] == "pytest":
            return subprocess.CompletedProcess(argv, 1, "assert failed")
        return subprocess.CompletedProcess(argv, 0, "")

    first = resume_cycle(state_path, "acme/widget#42", [["pytest", "-q"]], runner=fake_runner)
    assert first["status"] == "AWAITING_WEBGPT_FIX"
    assert first["iteration"] == 1

    second = resume_cycle(state_path, "acme/widget#42", [["pytest", "-q"]], runner=fake_runner)
    assert second["status"] == "BLOCKED_MAX_ITERATIONS"
    assert second["next_action"] == "Stop: max_iterations reached. Report to the user."
    assert json.loads(state_path.read_text())["status"] == "BLOCKED_MAX_ITERATIONS"


def test_create_repo_creates_private_repo_by_default_and_verifies_it():
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["gh", "repo", "create"]:
            return subprocess.CompletedProcess(
                argv, 0, "Created repository acme/widget on GitHub\nhttps://github.com/acme/widget\n"
            )
        if argv[:2] == ["gh", "api"]:
            payload = {"full_name": "acme/widget", "html_url": "https://github.com/acme/widget"}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload))
        return subprocess.CompletedProcess(argv, 0, "")

    result = create_repo("widget", runner=fake_runner)

    assert result == {"repository": "acme/widget", "html_url": "https://github.com/acme/widget"}
    assert calls[0] == ["gh", "repo", "create", "widget", "--private"]
    assert calls[1] == ["gh", "api", "repos/acme/widget"]


def test_create_repo_public_flag_passes_public_visibility():
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["gh", "repo", "create"]:
            return subprocess.CompletedProcess(argv, 0, "https://github.com/acme/widget\n")
        payload = {"full_name": "acme/widget", "html_url": "https://github.com/acme/widget"}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload))

    create_repo("widget", public=True, runner=fake_runner)

    assert calls[0] == ["gh", "repo", "create", "widget", "--public"]


def test_create_repo_rejects_unsafe_repository_name():
    with pytest.raises(ValueError, match="repository name"):
        create_repo("../evil", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, ""))


def test_create_repo_rejects_leading_dash_repository_name():
    with pytest.raises(ValueError, match="repository name"):
        create_repo("-h", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, ""))
    with pytest.raises(ValueError, match="repository name"):
        create_repo("--help", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, ""))


def test_create_repo_raises_when_gh_create_fails():
    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "name already exists on this account")

    with pytest.raises(RuntimeError, match="already exists"):
        create_repo("widget", runner=fake_runner)


def test_start_cycle_with_new_repo_provisions_and_embeds_repository(tmp_path):
    state_path = tmp_path / "cycle.json"
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["gh", "repo", "create"]:
            return subprocess.CompletedProcess(argv, 0, "https://github.com/acme/widget\n")
        payload = {"full_name": "acme/widget", "html_url": "https://github.com/acme/widget"}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload))

    result = start_cycle(
        "Add dark mode", state_path, intent_brief=INTENT_BRIEF, new_repo="widget", runner=fake_runner
    )

    assert "acme/widget" in result["webgpt_prompt"]
    assert "Do not create a new repository" in result["webgpt_prompt"]
    assert calls[0] == ["gh", "repo", "create", "widget", "--private"]


def test_start_cycle_without_new_repo_does_not_call_gh(tmp_path):
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "")

    start_cycle("Add dark mode", tmp_path / "cycle.json", intent_brief=INTENT_BRIEF, runner=fake_runner)

    assert calls == []


def test_validate_cycle_arguments_rejects_request_and_pr_together():
    with pytest.raises(ValueError, match="either --request or --pr"):
        validate_cycle_arguments(
            request="do it", intent_brief=INTENT_BRIEF, pr="acme/widget#1", new_repo=None, public=False
        )


def test_validate_cycle_arguments_rejects_new_repo_with_pr():
    with pytest.raises(ValueError, match="only valid with --request"):
        validate_cycle_arguments(
            request=None, intent_brief=None, pr="acme/widget#1", new_repo="widget", public=False
        )


def test_validate_cycle_arguments_rejects_public_with_pr():
    with pytest.raises(ValueError, match="only valid with --request"):
        validate_cycle_arguments(request=None, intent_brief=None, pr="acme/widget#1", new_repo=None, public=True)


def test_validate_cycle_arguments_requires_one_of_request_or_pr():
    with pytest.raises(ValueError, match="requires --request or --pr"):
        validate_cycle_arguments(request=None, intent_brief=None, pr=None, new_repo=None, public=False)


def test_validate_cycle_arguments_accepts_request_with_new_repo():
    validate_cycle_arguments(
        request="do it", intent_brief=INTENT_BRIEF, pr=None, new_repo="widget", public=True
    )


def test_start_cycle_prompt_requests_pr_url_trailer(tmp_path):
    result = start_cycle("Add dark mode", tmp_path / "cycle.json", intent_brief=INTENT_BRIEF)
    assert "PR_URL: <url>" in result["webgpt_prompt"]


def test_resume_cycle_handoff_requests_pr_url_trailer(tmp_path):
    state_path = tmp_path / "cycle.json"
    start_cycle("Add dark mode", state_path, intent_brief=INTENT_BRIEF)
    sha = "f" * 40

    def fake_runner(argv, **kwargs):
        if argv[:3] == ["gh", "api", "repos/acme/widget/pulls/42"]:
            payload = {"head": {"repo": {"full_name": "acme/widget"}, "ref": "feature", "sha": sha}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload))
        if argv[0] == "pytest":
            return subprocess.CompletedProcess(argv, 1, "assert failed")
        return subprocess.CompletedProcess(argv, 0, "")

    result = resume_cycle(state_path, "acme/widget#42", [["pytest", "-q"]], runner=fake_runner)

    assert "PR_URL: <url>" in result["webgpt_handoff"]
    # The GitHub-facing comment stays clean — no chat-only instruction leaks into the PR comment.
    assert "PR_URL: <url>" not in result["report"]["comment_markdown"]


def test_comment_markdown_includes_failure_output_when_present():
    report = build_report(
        repository="acme/widget",
        pull_request=42,
        head_sha="a" * 40,
        commands=[(["pytest", "-q"], 1, 0.25)],
        status="TEST_FAILED",
        comment_markdown="",
    )
    report["failure_output"] = "AssertionError: expected 1 got 2"
    markdown = _comment_markdown(report)
    assert "## Forge Loop PR test: TEST_FAILED" in markdown
    assert "AssertionError: expected 1 got 2" in markdown


def test_comment_markdown_omits_failure_section_when_absent():
    report = build_report(
        repository="acme/widget",
        pull_request=42,
        head_sha="a" * 40,
        commands=[(["pytest", "-q"], 0, 0.25)],
        status="TEST_PASSED",
        comment_markdown="",
    )
    markdown = _comment_markdown(report)
    assert "Failure output" not in markdown


def test_run_test_cycle_failure_output_reaches_comment_markdown():
    def fake_runner(argv, **kwargs):
        if argv[:2] == ["gh", "api"] and "pulls/9" in argv[2]:
            payload = {"head": {"repo": {"full_name": "acme/widget"}, "ref": "feature", "sha": "d" * 40}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload))
        if argv[0] == "pytest":
            return subprocess.CompletedProcess(argv, 1, "FAILED test_foo.py::test_bar - AssertionError: expected 1 got 2")
        return subprocess.CompletedProcess(argv, 0, "")

    report = run_test_cycle("acme/widget#9", [["pytest", "-q"]], runner=fake_runner)
    assert "AssertionError: expected 1 got 2" in report["comment_markdown"]
