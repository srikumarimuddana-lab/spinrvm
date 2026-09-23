import sys
from pathlib import Path

import pytest

# preauth_bots.py is also a directly executable script and imports sibling
# modules by their script-directory names.
sys.path.insert(0, str(Path(__file__).parent))
import preauth_bots


@pytest.mark.parametrize(
    ("base_url", "allowed"),
    [
        ("https://staging-api.spinr.ca", None),
        ("https://api.spinr.ca", "https://api.spinr.ca"),
    ],
)
def test_preauth_refuses_unapproved_or_production_target_before_session(
    monkeypatch, capsys, base_url, allowed
):
    if allowed is None:
        monkeypatch.delenv("LOADTEST_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("LOADTEST_ALLOWED_ORIGINS", allowed)
    monkeypatch.setattr(
        sys,
        "argv",
        ["preauth_bots.py", "--base-url", base_url, "--riders", "0", "--drivers", "0"],
    )

    def session_must_not_be_created():
        pytest.fail("preauth created an HTTP session before validating its target")

    monkeypatch.setattr(preauth_bots.requests, "Session", session_must_not_be_created)
    assert preauth_bots.main() == 2
    assert "REFUSING" in capsys.readouterr().err
