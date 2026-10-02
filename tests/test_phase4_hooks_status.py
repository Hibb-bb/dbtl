"""Hooks make the rules unskippable; status.py is the human's one command."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness import api
from harness.status import mid_flight, status_report

from conftest import REPO, hyp, plan, run_round, state

HOOKS = REPO / "hooks"


def hook(script: str, payload: dict, root: Path) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(payload), capture_output=True, text=True,
                       env={"CLAUDE_PROJECT_DIR": str(root), "PATH": "/usr/bin:/bin"})
    return p.returncode, p.stderr


@pytest.mark.parametrize("path", ["ledger/chain.jsonl", "ledger/raw/1/results.json", "./ledger/x", "ledger"])
def test_write_hook_blocks_ledger(root, path):
    code, err = hook("guard_write.py", {"tool_input": {"file_path": path}, "cwd": str(root)}, root)
    assert code == 2 and "ledger" in err


def test_write_hook_blocks_absolute_ledger_path(root):
    code, _ = hook("guard_write.py", {"tool_input": {"file_path": str(root / "ledger" / "chain.jsonl")}}, root)
    assert code == 2


@pytest.mark.parametrize("path", ["campaign.yaml", "notes/ledger_thoughts.md", "scripts/plan.py", "/tmp/ledger.txt"])
def test_write_hook_allows_other_paths(root, path):
    code, _ = hook("guard_write.py", {"tool_input": {"file_path": path}, "cwd": str(root)}, root)
    assert code == 0


@pytest.mark.parametrize("cmd", [
    "echo x > ledger/chain.jsonl",
    "cat foo >> ./ledger/chain.jsonl",
    "echo x | tee ledger/raw/1/results.json",
    "sed -i '' 's/a/b/' ledger/chain.jsonl",
    "rm -rf ledger/raw",
    "mv ledger/chain.jsonl /tmp/x",
    "cp /tmp/x ledger/chain.jsonl",
    "truncate -s 0 ledger/chain.jsonl",
    "python3 -c 'open(\"ledger/chain.jsonl\",\"w\").write(\"\")'",
    "echo x > \"$CLAUDE_PROJECT_DIR\"/ledger/chain.jsonl",
    "git checkout -- ledger/chain.jsonl",
])
def test_bash_hook_blocks_ledger_writes(root, cmd):
    code, err = hook("guard_bash.py", {"tool_input": {"command": cmd}}, root)
    assert code == 2, cmd
    assert "append-only" in err


@pytest.mark.parametrize("cmd", [
    "cat ledger/chain.jsonl | tail -3",
    "jq -c .kind ledger/chain.jsonl",
    "uv run python scripts/status.py",
    "uv run python scripts/hypothesize.py h.json",
    "ls -la ledger/raw/1",
    "echo ledger > notes.txt",
    "grep killed ledger/chain.jsonl > /tmp/kills.txt",
    "python3 scripts/plan.py plan.json > /tmp/out.txt",
])
def test_bash_hook_allows_reads_and_scripts(root, cmd):
    code, _ = hook("guard_bash.py", {"tool_input": {"command": cmd}}, root)
    assert code == 0, cmd


def test_hooks_settings_wires_all_three():
    s = json.loads((HOOKS / "settings.json").read_text())
    h = s["hooks"]
    matchers = {x["matcher"]: x["hooks"][0]["command"] for x in h["PreToolUse"]}
    assert any("Write" in m and "Edit" in m for m in matchers)
    assert "Bash" in matchers and "guard_bash.py" in matchers["Bash"]
    assert "stop_guard.sh" in h["Stop"][0]["hooks"][0]["command"]
    assert json.loads((REPO / ".claude" / "settings.json").read_text()) == s


def test_stop_guard_blocks_mid_flight(root):
    assert mid_flight(root)[0] is False  # fresh: clean boundary
    api.hypothesize(root, hyp(root))
    assert mid_flight(root)[0] is True
    api.plan(root, plan(root)); api.submit(root)
    blocked, why = mid_flight(root)
    assert blocked and "collect" in why
    api.collect(root)
    assert mid_flight(root)[0] is True
    api.review(root)
    assert mid_flight(root)[0] is False


def test_stop_guard_script_exit_code(root):
    env = {"CLAUDE_PROJECT_DIR": str(root), "PATH": "/usr/bin:/bin"}
    # the shell wrapper resolves python relative to the campaign root; point it at this interpreter
    (root / ".venv" / "bin").mkdir(parents=True)
    wrapper = root / ".venv" / "bin" / "python"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n'); wrapper.chmod(0o755)
    (root / "scripts").symlink_to(REPO / "scripts")
    (root / "harness").symlink_to(REPO / "harness")
    (root / "adapters").symlink_to(REPO / "adapters")
    (root / "schemas").symlink_to(REPO / "schemas")
    (root / "baselines").symlink_to(REPO / "baselines")
    ok = subprocess.run(["sh", str(HOOKS / "stop_guard.sh")], env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    api.hypothesize(root, hyp(root)); api.plan(root, plan(root)); api.submit(root)
    blocked = subprocess.run(["sh", str(HOOKS / "stop_guard.sh")], env=env, capture_output=True, text=True)
    assert blocked.returncode == 2 and "STOP BLOCKED" in blocked.stderr


def test_status_report_content(root):
    run_round(root); run_round(root)
    text, info = status_report(root)
    assert info["chain_ok"] and info["round"] == 3 and info["budget_left"] == 4
    assert "Chain integrity: OK" in text and "Round 3 of 3" in text
    assert "H1" in text and "age=" in text
    assert "hit rate" in text and info["calibration"]["n"] == 8  # 2 rounds x 4 picks, none failed
    assert "random" in text and "bayes_opt" in text and "agent" in text
    assert info["roles"] == {"exploit": 4, "discriminate": 2, "explore": 2}


def test_status_reports_broken_chain(root):
    path = root / "ledger" / "chain.jsonl"
    lines = path.read_text().splitlines(); lines[1] = lines[1].replace('"kind":"result"', '"kind":"rejection"')
    path.write_text("\n".join(lines) + "\n")
    text, info = status_report(root)
    assert not info["chain_ok"] and "BROKEN" in text and "line 2" in text
    assert mid_flight(root)[0] is True  # a broken chain also blocks stopping


def test_status_cli_exit_codes(root):
    env = {"DBTL_ROOT": str(root), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "status.py")], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "Next step: hypothesize" in r.stdout
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "status.py"), "--stop-check"], env=env, capture_output=True, text=True)
    assert r.returncode == 0
