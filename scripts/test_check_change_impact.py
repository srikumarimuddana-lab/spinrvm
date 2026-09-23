from scripts import check_change_impact as gate


def record(paths):
    files = "\n".join(f"| `{p}` | Changed | Reason |" for p in paths)
    return ("## Root cause\nLease released before database confirmation.\n"
            "## Risk & impact\nMay delay retries in shared dispatch.\n"
            "## User experience\nDrivers may see offers later during recovery.\n"
            "## Rollback plan\nRevert code; no live data changes.\n"
            "## Verification performed\nFocused tests passed; staging not run.\n"
            f"## Files modified\n{files}\n")


def run_gate(monkeypatch, tmp_path, paths, body="", logs=()):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BASE_SHA", "base")
    monkeypatch.setenv("HEAD_SHA", "head")
    monkeypatch.setenv("PR_BODY", body)
    changed = list(paths)
    for i, text in enumerate(logs):
        path = f"docs/change-log/log{i}.md"
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        changed.append(path)
    monkeypatch.setattr(gate, "changed_paths", lambda *_: changed)
    return gate.main()


def test_sensitive_markers_include_money_loops_and_shared_redis():
    paths = ["backend/utils/payment_retry.py", "backend/utils/preauth_capture.py",
             "backend/utils/orphaned_hold_reconciler.py", "backend/utils/auto_payout.py",
             "backend/utils/corporate_autotopup.py", "backend/utils/redis_client.py"]
    assert all(gate.is_sensitive([path]) for path in paths)
    assert gate.check_change_impact(["docs/readme.md"], []) == []


def test_body_or_log_and_union_of_valid_logs(monkeypatch, tmp_path):
    a, b = "backend/routes/payments.py", "backend/routes/rides.py"
    assert run_gate(monkeypatch, tmp_path, [a], logs=[record([a])]) == 0
    assert run_gate(monkeypatch, tmp_path, [a], body=record([a])) == 0
    assert run_gate(monkeypatch, tmp_path, [a, b], logs=[record([a]), record([b]), record(["docs/other.md"])]) == 0


def test_placeholders_and_uncovered_path_fail(monkeypatch, tmp_path, capsys):
    a, b = "backend/routes/payments.py", "backend/routes/rides.py"
    incomplete = record([a]).replace("Focused tests passed; staging not run.", "[ ] Automated tests run (list which)")
    assert run_gate(monkeypatch, tmp_path, [a, b], body=incomplete) == 1
    assert b in capsys.readouterr().out
    assert gate.check_change_impact([a], [incomplete]) == [a]
