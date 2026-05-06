# /plan — Decompose before coding

Before writing a single line of implementation, produce a decomposition of the task.

## Steps

1. **Understand scope** — read only the directly relevant files (use `grep` + targeted `Read` with `offset`+`limit`). Do not read entire large files.

2. **List subtasks** — output a numbered list where each subtask:
   - touches ≤ 3 files
   - has a clear one-line goal
   - can be committed independently

3. **Identify risks** — for each subtask, flag: state machine changes, money arithmetic, auth, DB migrations, or WebSocket events. These need extra review.

4. **Estimate token cost** — if the total plan has > 8 subtasks, ask the user to confirm before proceeding.

5. **Use TodoWrite** — register every subtask before starting the first one.

6. **Start** — implement subtask 1 only. Wait for user confirmation (or `/start` trigger) before proceeding to subtask 2.

## Output format

```
Plan: <task name>

Subtasks:
1. [ ] <file(s)> — <goal>
2. [ ] <file(s)> — <goal>
...

Risks: <list or "none">
Estimated subtasks: N
```
