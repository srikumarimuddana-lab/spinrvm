# Review Queue

**Pending:** 1
**Oldest staged:** 2026-05-04T00:02:54.175468+00:00

Run `python .agent/tools/list_candidates.py` for detail, then:
- `python .agent/tools/graduate.py <id> --rationale "..."` to accept
- `python .agent/tools/reject.py <id> --reason "..."` to reject
- Review in a batch so cross-candidate contradictions are caught.

## Priority order (top 10)

- **6e3cc3fc1443** (priority=25.78, size=2, rejections=0) — FAILURE in claude-code: Command failed: cd /usr/local/lib/hermes-agent && uv ven
