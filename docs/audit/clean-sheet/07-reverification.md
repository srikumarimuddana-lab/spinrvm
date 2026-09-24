# 07 — Independent re-verification of a finding sample (Step 6)

Report-only. No code, config, or data was changed to produce this file.

## §0 Method

- **Sample selection.** Seeded random draw (seed 20260924), one finding card per report: the 15 lane reports in `02-findings/` plus `03-benchmark.md`, 16 cards in total (about 12% of the 137 finding cards). The nine claims the orchestrator had already hand-checked were excluded from the draw so this sample does not overlap them.
- **Independence.** The re-verifier runs on a different model from the one that wrote the lane reports. For each card, every cited `path:line` was opened fresh and the code was read directly; quotes in the cards were not relied on. Every absence claim ("no test", "no UI", "never called", "not listed") was re-checked with my own greps using several spellings and file-name patterns, because an earlier absence claim (QUAL-003) turned out to be false.
- **Tools.** Read, Grep and Bash (`grep`, `rg`, `sed -n`, `git log`) on the working tree as of 2026-09-24. No database, vendor console, or live-environment access was used. Competitor claims in BENCH-006 were checked only as far as public web pages could be reached.
- **Verdicts.** CONFIRMED (every material claim holds); PARTIALLY CONFIRMED (the core claim holds but a material detail is wrong); REFUTED (the core claim is false); UNVERIFIABLE (needs live data, a database, or a vendor console). Citation drift (right claim, wrong line number) is recorded separately and does not count as a refutation.
- **Limits.** A sample of 16 gives a rough error rate, not a precise one. Claims about live state (production DB contents, vendor dashboards) are marked unverifiable rather than guessed at.

