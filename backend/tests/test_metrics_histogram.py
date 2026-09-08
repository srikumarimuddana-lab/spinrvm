"""Histogram support in utils/metrics.py (observe / time_ms / exposition).

These back the KPI timing metrics (offer-to-accept, fare calc, WS fan-out)
so the SLA table in CLAUDE.md is measurable from /metrics.
"""

from __future__ import annotations

import pytest

from backend.utils import metrics


@pytest.fixture(autouse=True)
def _clean_histograms():
    with metrics._lock:
        metrics._histograms.clear()
    yield
    with metrics._lock:
        metrics._histograms.clear()


class TestObserve:
    def test_sum_and_count_accumulate(self):
        metrics.observe("test_duration_ms", 40.0)
        metrics.observe("test_duration_ms", 60.0)
        snap = metrics.snapshot()
        cell = snap["histograms"]["test_duration_ms"][()]
        assert cell["count"] == 2
        assert cell["sum"] == pytest.approx(100.0)

    def test_cumulative_buckets(self):
        # buckets 10 / 100: value 50 lands in every le >= 100
        metrics.observe("test_b_ms", 50.0, buckets=(10, 100))
        cell = metrics.snapshot()["histograms"]["test_b_ms"][()]
        assert cell["buckets"] == [0, 1]

    def test_labels_create_separate_series(self):
        metrics.observe("test_l_ms", 5.0, {"outcome": "success"})
        metrics.observe("test_l_ms", 7.0, {"outcome": "failed"})
        series = metrics.snapshot()["histograms"]["test_l_ms"]
        assert len(series) == 2

    def test_value_above_all_buckets_only_in_count(self):
        metrics.observe("test_inf_ms", 99999.0, buckets=(10, 100))
        cell = metrics.snapshot()["histograms"]["test_inf_ms"][()]
        assert cell["buckets"] == [0, 0]
        assert cell["count"] == 1


class TestTimeMs:
    def test_records_elapsed(self):
        with metrics.time_ms("test_t_ms"):
            pass
        cell = metrics.snapshot()["histograms"]["test_t_ms"][()]
        assert cell["count"] == 1
        assert cell["sum"] >= 0.0

    def test_records_on_exception(self):
        with pytest.raises(ValueError):
            with metrics.time_ms("test_exc_ms"):
                raise ValueError("boom")
        cell = metrics.snapshot()["histograms"]["test_exc_ms"][()]
        assert cell["count"] == 1


class TestTimedDecorator:
    @pytest.mark.anyio
    async def test_async_function_observed_and_signature_preserved(self):
        @metrics.timed("test_dec_ms")
        async def handler(x: int) -> int:
            return x + 1

        assert await handler(1) == 2
        cell = metrics.snapshot()["histograms"]["test_dec_ms"][()]
        assert cell["count"] == 1
        # FastAPI resolves dependencies via the original signature.
        assert handler.__wrapped__.__name__ == "handler"

    @pytest.mark.anyio
    async def test_records_when_handler_raises(self):
        @metrics.timed("test_dec_exc_ms")
        async def handler():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await handler()
        cell = metrics.snapshot()["histograms"]["test_dec_exc_ms"][()]
        assert cell["count"] == 1


class TestExposition:
    def test_render_histogram_lines(self):
        import os

        metrics.observe("test_r_ms", 30.0, {"path": "fare"}, buckets=(10, 100))
        out = metrics.render_prometheus()
        pid = os.getpid()
        assert "# TYPE test_r_ms histogram" in out
        assert f'test_r_ms_bucket{{le="10",path="fare",worker_pid="{pid}"}} 0' in out
        assert f'test_r_ms_bucket{{le="100",path="fare",worker_pid="{pid}"}} 1' in out
        assert f'test_r_ms_bucket{{le="+Inf",path="fare",worker_pid="{pid}"}} 1' in out
        assert f'test_r_ms_sum{{path="fare",worker_pid="{pid}"}} 30.0' in out
        assert f'test_r_ms_count{{path="fare",worker_pid="{pid}"}} 1' in out

    def test_counters_and_gauges_unaffected(self):
        metrics.inc("test_plain_total")
        out = metrics.render_prometheus()
        line = next(line for line in out.splitlines() if line.startswith("test_plain_total{"))
        assert line.endswith("} 1")


class TestWorkerPidLabel:
    """C82: every exposed series must carry worker_pid so a multi-process
    Uvicorn deployment doesn't smear one worker's counters into another's
    on each scrape (see the docstring on render_prometheus)."""

    def test_counter_gets_worker_pid_label(self):
        import os

        metrics.inc("test_pid_total")
        out = metrics.render_prometheus()
        assert f'test_pid_total{{worker_pid="{os.getpid()}"}} 1' in out

    def test_existing_labels_are_preserved_alongside_worker_pid(self):
        import os

        metrics.inc("test_pid_labeled_total", {"outcome": "success"})
        out = metrics.render_prometheus()
        assert 'outcome="success"' in out
        assert f'worker_pid="{os.getpid()}"' in out

    def test_gauge_gets_worker_pid_label(self):
        import os

        metrics.set_gauge("test_pid_gauge", 3.0)
        out = metrics.render_prometheus()
        assert f'test_pid_gauge{{worker_pid="{os.getpid()}"}} 3.0' in out

    def test_histogram_bucket_and_sum_lines_get_worker_pid_label(self):
        import os

        metrics.observe("test_pid_hist_ms", 5.0, buckets=(10, 100))
        out = metrics.render_prometheus()
        pid = os.getpid()
        assert f'le="10",worker_pid="{pid}"' in out
        assert f'test_pid_hist_ms_sum{{worker_pid="{pid}"}}' in out
        assert f'test_pid_hist_ms_count{{worker_pid="{pid}"}}' in out

    def test_caller_supplied_worker_pid_label_is_not_duplicated(self):
        """A caller-supplied `worker_pid` label must be replaced, not
        duplicated -- two `worker_pid="..."` entries in one `{...}` block is
        invalid Prometheus exposition format and risks a scrape-wide parse
        failure rather than one bad series (flagged by review)."""
        import os

        metrics.inc("test_pid_collision_total", {"worker_pid": "bogus"})
        out = metrics.render_prometheus()
        line = next(line for line in out.splitlines() if line.startswith("test_pid_collision_total{"))
        assert line.count("worker_pid=") == 1
        assert f'worker_pid="{os.getpid()}"' in line
        assert 'worker_pid="bogus"' not in line
