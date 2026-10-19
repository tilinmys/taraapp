"""AWS costs tab backend (sakhi_aws_costs) against a fake Cost Explorer."""

import datetime as dt

import pytest

import sakhi_aws_costs as costs


def _m(amount):
    return {"UnblendedCost": {"Amount": str(amount), "Unit": "USD"}}


class FakeCE:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def get_cost_and_usage(self, **kw):
        self.calls.append(("usage", kw))
        if self.fail:
            raise self.fail
        start = kw["TimePeriod"]["Start"]
        if kw["Granularity"] == "MONTHLY" and "GroupBy" not in kw:   # credits
            return {"ResultsByTime": [
                {"TimePeriod": {"Start": start}, "Total": _m(-13.0)},
                {"TimePeriod": {"Start": "2026-09-01"}, "Total": _m(-10.25)}]}
        if kw["Granularity"] == "MONTHLY":
            key = kw["GroupBy"][0]["Key"]
            names = ["Amazon Elastic Compute Cloud - Compute", "Amazon Simple Storage Service"] \
                if key == "SERVICE" else ["APS3-BoxUsage:t3.small", "APS3-EBS:VolumeUsage.gp3"]
            return {"ResultsByTime": [
                {"TimePeriod": {"Start": start}, "Groups": [
                    {"Keys": [names[0]], "Metrics": _m(12.5)}, {"Keys": [names[1]], "Metrics": _m(0.5)}]},
                {"TimePeriod": {"Start": "2026-09-01"}, "Groups": [
                    {"Keys": [names[0]], "Metrics": _m(10.0)}, {"Keys": [names[1]], "Metrics": _m(0.25)}]},
            ]}
        return {"ResultsByTime": [
            {"TimePeriod": {"Start": f"2026-09-{d:02d}"}, "Total": _m(0.4)} for d in range(1, 27)]}

    def get_cost_forecast(self, **kw):
        self.calls.append(("forecast", kw))
        return {"Total": {"Amount": "1.6"}}


@pytest.fixture
def ce(monkeypatch, tmp_path):
    fake = FakeCE()
    monkeypatch.setattr(costs, "_CLIENT_FACTORY", lambda: fake)
    monkeypatch.setattr(costs, "_today", lambda: dt.date(2026, 9, 26))
    monkeypatch.setenv("AWS_COST_CACHE_PATH", str(tmp_path / "c.json"))
    costs._MEMORY.clear()
    yield fake
    costs._MEMORY.clear()


def test_overview_numbers(ce):
    o = costs.overview()
    assert o["available"] is True
    assert o["month_to_date"] == 10.25            # current-month groups only
    assert o["last_month"] == 13.0
    assert o["forecast_month_end"] == 11.85       # MTD + remaining forecast
    assert o["services"][0]["service"].startswith("Amazon Elastic Compute")
    assert len(o["daily"]) == 26
    # Credits never hide usage: usage is the headline, credits separate.
    assert o["credits_month_to_date"] == -10.25
    assert o["net_payable_month_to_date"] == 0.0
    usage_calls = [c[1] for c in ce.calls if c[0] == "usage" and "GroupBy" in c[1]]
    assert usage_calls[0]["Filter"] == costs._EXCLUDE_CREDITS
    forecast = [c[1] for c in ce.calls if c[0] == "forecast"][0]
    assert forecast["Metric"] == "UNBLENDED_COST"
    # Periods: last month start .. tomorrow (End is exclusive)
    first = ce.calls[0][1]["TimePeriod"]
    assert first == {"Start": "2026-08-01", "End": "2026-09-27"}


def test_cache_and_refresh_floor_limit_paid_requests(ce, monkeypatch):
    costs.overview()
    n = len(ce.calls)
    assert costs.overview()["cached"] is True             # served from cache
    assert costs.overview(force=True)["cached"] is True   # inside 15-min floor
    assert len(ce.calls) == n                              # no new $0.01 calls
    monkeypatch.setenv("AWS_COST_MIN_REFRESH_SECONDS", "0")
    assert costs.overview(force=True)["cached"] is False
    assert len(ce.calls) > n


def test_access_denied_is_reported_not_raised(monkeypatch, tmp_path):
    fake = FakeCE(fail=Exception("An error occurred (AccessDeniedException): not authorized"))
    monkeypatch.setattr(costs, "_CLIENT_FACTORY", lambda: fake)
    monkeypatch.setenv("AWS_COST_CACHE_PATH", str(tmp_path / "c.json"))
    costs._MEMORY.clear()
    o = costs.overview()
    assert o == {"available": False, "error_kind": "access_denied",
                 "message": "An error occurred (AccessDeniedException): not authorized"}


def test_failed_refresh_keeps_last_good_numbers(ce, monkeypatch):
    good = costs.overview()
    monkeypatch.setenv("AWS_COST_MIN_REFRESH_SECONDS", "0")
    ce.fail = Exception("throttled")
    again = costs.overview(force=True)
    assert again["stale"] is True and again["month_to_date"] == good["month_to_date"]
    assert again["refresh_error"]["error_kind"] == "error"


def test_service_explorer_breaks_down_by_usage_type(ce):
    d = costs.service_detail("Amazon Elastic Compute Cloud - Compute")
    assert d["available"] and d["usage_types"][0]["usage_type"] == "APS3-BoxUsage:t3.small"
    assert d["month_to_date"] == 10.25
    flt = ce.calls[0][1]["Filter"]["And"]
    assert flt[0]["Dimensions"] == {"Key": "SERVICE", "Values": ["Amazon Elastic Compute Cloud - Compute"]}
    assert flt[1] == costs._EXCLUDE_CREDITS
    assert costs.service_detail("")["error_kind"] == "bad_request"


def test_cache_survives_restart(ce):
    costs.overview()
    costs._MEMORY.clear()                 # simulate a new process
    n = len(ce.calls)
    assert costs.overview()["cached"] is True
    assert len(ce.calls) == n
