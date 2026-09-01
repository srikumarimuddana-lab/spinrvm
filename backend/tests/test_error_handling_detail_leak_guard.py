"""WS-E / C6 drift guard: no route may interpolate an exception into `detail`.

`utils/error_handling.http_exception_handler` redacts every 4xx string detail,
so a `detail=str(e)` that slips in is no longer an immediate breach. This guard
exists because that safety net is a *second* layer, and relying on it alone is
fragile in two specific ways:

- It cannot help a 5xx, where the detail is replaced wholesale — the route
  author loses the message entirely instead of choosing a useful fixed one.
- Redaction is pattern-based. It catches the identifier shapes we know about
  (SIN, email, phone, GPS, PAN, path, token, date); it cannot catch a leak
  shaped like prose — a Supabase constraint name, an internal column list, a
  vendor stack frame.

So the rule stays "route authors call `client_safe_detail(e, fallback=...)` or
write a fixed message", and this test holds that line.

ALLOWLIST IS EMPTY AND MUST STAY EMPTY. If a new site genuinely cannot use the
helper, the fix is to extend the helper, not to add an entry here.
"""

import io
import pathlib
import re
import tokenize

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Paths (repo-relative, POSIX) permitted to interpolate an exception into a
#: `detail=`. Empty by design — see the module docstring.
_ALLOWLIST: frozenset[str] = frozenset()

#: `detail=str(e)` / `detail=str(exc)` / `detail=str(e.message)`
_DETAIL_STR_RE = re.compile(r"detail=str\(")
#: `detail=f"... {e} ..."` / `{exc}` / `{e.message}` — an f-string detail that
#: interpolates the caught exception itself. `{redact_client_text(e)}` and
#: `{client_safe_detail(...)}` do not match: the brace is followed by a call.
_DETAIL_FSTRING_RE = re.compile(r"""detail=f["'][^"']*\{(?:e|exc|err)[.}]""")

_SEARCH_ROOTS = ("routes", "services", "utils")


def _python_sources():
    for root in _SEARCH_ROOTS:
        for path in sorted((_ROOT / root).rglob("*.py")):
            yield path
    yield _ROOT / "documents.py"


def _prose_spans(text: str) -> set[tuple[int, int]]:
    """(line, col) starts covered by a comment or string literal.

    Needed because this rule is *documented* in prose that quotes the very
    pattern it forbids — ``utils/pii.py`` and ``utils/error_handling.py`` both
    explain themselves using ``detail=str(e)``. A line-regex guard flags its own
    documentation, and the usual fix (allowlist those files) would blind the
    guard to real code in them. Matching on code tokens only is exact instead.
    """
    spans: set[tuple[int, int]] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in tokens:
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                continue
            srow, scol = tok.start
            erow, ecol = tok.end
            for row in range(srow, erow + 1):
                lo = scol if row == srow else 0
                hi = ecol if row == erow else 1 << 30
                for col in range(lo, min(hi, 4096)):
                    spans.add((row, col))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # An unparseable file is a separate failure the test suite will surface;
        # fall back to flagging everything rather than silently passing it.
        return set()
    return spans


def _offenders() -> list[str]:
    hits: list[str] = []
    for path in _python_sources():
        if not path.exists():
            continue
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        prose = _prose_spans(text)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for rx in (_DETAIL_STR_RE, _DETAIL_FSTRING_RE):
                m = rx.search(line)
                # `detail=` is always code even when an f-string follows, so the
                # match START is the right thing to classify.
                if m and (lineno, m.start()) not in prose:
                    hits.append(f"{rel}:{lineno}: {line.strip()}")
                    break
    return hits


class TestNoRawExceptionInDetail:
    def test_no_route_interpolates_an_exception_into_detail(self):
        offenders = _offenders()
        assert not offenders, (
            "These sites hand raw exception text to a client. Use "
            "`client_safe_detail(e, fallback=...)` (utils/pii.py) or a fixed "
            "message instead:\n  " + "\n  ".join(offenders)
        )

    def test_allowlist_is_empty(self):
        # A regression here means someone widened the guard rather than fixing
        # the site. Deliberate tripwire.
        assert _ALLOWLIST == frozenset()

    def test_guard_patterns_actually_match_the_shapes_they_claim(self):
        # Without this, a typo'd regex would make the guard above vacuously
        # pass and the whole file would be decoration.
        assert _DETAIL_STR_RE.search("raise HTTPException(422, detail=str(e))")
        assert _DETAIL_FSTRING_RE.search('detail=f"row {e} failed"')
        assert _DETAIL_FSTRING_RE.search('detail=f"{exc}"')
        assert _DETAIL_FSTRING_RE.search('detail=f"{e.message}"')
        # ...and do not match the approved replacements.
        assert not _DETAIL_STR_RE.search('detail=client_safe_detail(e, fallback="x")')
        assert not _DETAIL_FSTRING_RE.search('detail=f"{label}: {redact_client_text(e)}"')
        assert not _DETAIL_FSTRING_RE.search('detail=f"CSV exceeds the {MAX} KB limit"')
