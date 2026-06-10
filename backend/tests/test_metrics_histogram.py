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
        metrics.observe("test_r_ms", 30.0, {"path": "fare"}, buckets=(10, 100))
        out = metrics.render_prometheus()
        assert "# TYPE test_r_ms histogram" in out
        assert 'test_r_ms_bucket{le="10",path="fare"} 0' in out
        assert 'test_r_ms_bucket{le="100",path="fare"} 1' in out
        assert 'test_r_ms_bucket{le="+Inf",path="fare"} 1' in out
        assert 'test_r_ms_sum{path="fare"} 30.0' in out
        assert 'test_r_ms_count{path="fare"} 1' in out

    def test_counters_and_gauges_unaffected(self):
        metrics.inc("test_plain_total")
        out = metrics.render_prometheus()
        assert "test_plain_total 1" in out
