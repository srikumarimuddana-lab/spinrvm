# PR 5722 completion request normalization

Issue: direct `complete_ride` callers can receive FastAPI's `Body` sentinel when omitting the optional body, so checking request fields before normalization raises `AttributeError`.

Fix: normalize the optional request once before the progress gate and reuse the parsed model. Existing persisted stop IDs or completion fields keep the gate active if an older client attempts to complete a ride whose stop progression has already started; untouched legacy stop JSON remains compatible during mobile rollout.

Blast radius: driver completion request parsing and pending-stop enforcement. Alternative considered: force the gate on every legacy ride, which would strand installed clients without stop actions.

Verification: `/tmp/pr5722-venv/bin/python -m pytest -o addopts= backend/tests/test_ride_complete_coverage.py backend/tests/test_driver_stop_progress.py backend/tests/test_rides.py -q` passed (93 tests).

Rollback: revert this normalization and condition together. No schema or stored data change. No live DB or device test was run.
