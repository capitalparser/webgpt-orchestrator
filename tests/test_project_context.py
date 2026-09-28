import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "project_context.py"


def invoke(tmp_path, *args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--registry", str(tmp_path / "registry")],
        capture_output=True, text=True, check=False,
    )
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout)


def test_first_use_missing_then_reuses_binding_across_tasks(tmp_path):
    code, missing = invoke(tmp_path, "lookup", "--repository", "Acme/Widget")
    assert code == 0
    assert missing["status"] == "PROJECT_NOT_LINKED"
    assert not (tmp_path / "registry").exists()
    code, bound = invoke(
        tmp_path, "bind", "--repository", "Acme/Widget",
        "--project-url", "https://chatgpt.com/g/g-p-example/project",
        "--project-name", "Existing product research",
    )
    assert code == 0
    code, found = invoke(tmp_path, "lookup", "--repository", "acme/widget")
    assert code == 0
    assert found["status"] == "PROJECT_LINKED"
    assert found["repository"] == "acme/widget"
    assert found["project_url"] == bound["project_url"]
    assert found["project_name"] == "Existing product research"


def test_attach_preserves_state_and_refuses_repository_or_project_switch(tmp_path):
    project = "https://chatgpt.com/g/g-p-example/project"
    invoke(tmp_path, "bind", "--repository", "acme/widget", "--project-url", project, "--project-name", "Widget")
    state = tmp_path / "task.json"
    original = {"request": "fix widget", "iteration": 2, "custom": {"keep": True}}
    state.write_text(json.dumps(original))
    code, _ = invoke(tmp_path, "attach", "--repository", "acme/widget", "--state", str(state))
    assert code == 0
    saved = json.loads(state.read_text())
    assert all(saved[key] == value for key, value in original.items())
    assert saved["project_url"] == project
    assert saved["repository"] == "acme/widget"
    conversation = "https://chatgpt.com/g/g-p-example/c/task-1"
    code, _ = invoke(tmp_path, "attach", "--repository", "acme/widget", "--state", str(state), "--conversation-url", conversation)
    assert code == 0
    before = state.read_bytes()
    code, _ = invoke(tmp_path, "attach", "--repository", "acme/widget", "--state", str(state), "--conversation-url", "https://chatgpt.com/g/g-p-other/c/task-2")
    assert code == 2
    assert state.read_bytes() == before

    code, _ = invoke(tmp_path, "bind", "--repository", "acme/widget", "--project-url", "https://chatgpt.com/g/g-p-other/project", "--project-name", "Wrong")
    assert code == 2
    invoke(tmp_path, "bind", "--repository", "acme/other", "--project-url", project, "--project-name", "Widget")
    code, _ = invoke(tmp_path, "attach", "--repository", "acme/other", "--state", str(state))
    assert code == 2
    assert state.read_bytes() == before


@pytest.mark.parametrize("repository", ["../widget", "acme/..", "/acme/widget", "acme/widget/extra"])
def test_invalid_repository_does_not_create_records(tmp_path, repository):
    code, result = invoke(tmp_path, "bind", "--repository", repository,
                          "--project-url", "https://chatgpt.com/g/g-p-good/project", "--project-name", "Good")
    assert code == 2
    assert result["status"] == "BLOCKED_PROJECT_UNAVAILABLE"
    assert not (tmp_path / "registry").exists()


@pytest.mark.parametrize("url", [
    "https://chatgpt.com.evil/g/g-p-good/project",
    "http://chatgpt.com/g/g-p-good/project",
    "https://chatgpt.com/c/chat-1",
    "https://chatgpt.com/g/g-p-good/project?redirect=elsewhere",
])
def test_invalid_project_url_does_not_create_records(tmp_path, url):
    code, _ = invoke(tmp_path, "bind", "--repository", "acme/widget", "--project-url", url, "--project-name", "Good")
    assert code == 2
    assert not (tmp_path / "registry").exists()


def test_corrupt_mapping_is_blocked_not_treated_as_absent(tmp_path):
    record = tmp_path / "registry/acme/widget.json"
    record.parent.mkdir(parents=True)
    record.write_text('{"repository":')
    before = record.read_bytes()
    code, result = invoke(tmp_path, "lookup", "--repository", "acme/widget")
    assert code == 2
    assert result["status"] == "BLOCKED_PROJECT_UNAVAILABLE"
    assert record.read_bytes() == before


def test_missing_mapping_cannot_attach_or_create_task(tmp_path):
    state = tmp_path / "task.json"
    code, _ = invoke(tmp_path, "attach", "--repository", "acme/widget", "--state", str(state))
    assert code == 2
    assert not state.exists()


@pytest.mark.parametrize("invalid", [{"project_url": 4}, {"conversation_url": 4}])
def test_invalid_task_routing_blocks_without_modification(tmp_path, invalid):
    invoke(tmp_path, "bind", "--repository", "acme/widget",
           "--project-url", "https://chatgpt.com/g/g-p-good/project", "--project-name", "Good")
    state = tmp_path / "task.json"
    state.write_text(json.dumps(invalid))
    before = state.read_bytes()
    code, result = invoke(tmp_path, "attach", "--repository", "acme/widget", "--state", str(state))
    assert code == 2
    assert result["status"] == "BLOCKED_PROJECT_UNAVAILABLE"
    assert state.read_bytes() == before


def test_concurrent_bindings_cannot_overwrite_each_other(tmp_path):
    processes = [subprocess.Popen(
        [sys.executable, str(SCRIPT), "bind", "--repository", "acme/widget",
         "--project-url", f"https://chatgpt.com/g/g-p-{name}/project",
         "--project-name", name, "--registry", str(tmp_path / "registry")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for name in ("first", "second")]
    outputs = [json.loads(process.communicate()[0]) for process in processes]
    assert sorted(process.returncode for process in processes) == [0, 2]
    winner = next(output for output in outputs if output["status"] == "PROJECT_LINKED")
    code, record = invoke(tmp_path, "lookup", "--repository", "acme/widget")
    assert code == 0
    assert record["project_url"] == winner["project_url"]


def test_legacy_task_pr_cannot_be_attached_to_another_repository(tmp_path):
    invoke(tmp_path, "bind", "--repository", "acme/widget",
           "--project-url", "https://chatgpt.com/g/g-p-good/project", "--project-name", "Good")
    state = tmp_path / "task.json"
    state.write_text(json.dumps({"pr_ref": "acme/other#1"}))
    before = state.read_bytes()
    code, _ = invoke(tmp_path, "attach", "--repository", "acme/widget", "--state", str(state))
    assert code == 2
    assert state.read_bytes() == before
