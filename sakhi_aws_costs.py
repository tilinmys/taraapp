"""AWS spend for the admin dashboard's "AWS costs" tab (2026-09-26).

Reads the real bill from AWS Cost Explorer - not an estimate. Two views:

  overview()             month to date, last month, month-end forecast,
                         last 30 days by day, and every service's spend
  service_detail(name)   one service broken down by usage type, plus its
                         own last-30-days trend (the dashboard's Explore column)

Cost Explorer bills $0.01 per API request and its data lags ~24 h, so every
result is cached (memory + a JSON file that survives restarts):
  AWS_COST_CACHE_SECONDS        automatic refresh interval (default 6 h)
  AWS_COST_MIN_REFRESH_SECONDS  floor for a manual "Refresh" (default 15 min)
An overview refresh is 4 requests and a service drill-down 2, so normal use
stays at a few cents a day.

Needs IAM on the instance role: ce:GetCostAndUsage and ce:GetCostForecast.
Without it the endpoints return {"available": false, ...} with the reason,
and the dashboard shows how to enable it - never an exception.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("sakhi.aws_costs")

_METRIC = "UnblendedCost"
# Usage BEFORE credits. Measured 2026-09-26: this account's month showed
# Usage $36.77, Credit -$36.77, net $0.00 - an unfiltered total would read
# "$0" and hide the real spend until the credits run out. Credits and the
# net amount payable are reported separately.
_EXCLUDE_CREDITS = {"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}}
_LOCK = threading.Lock()
_MEMORY: dict[str, dict] = {}
_CLIENT_FACTORY: Callable[[], Any] | None = None   # tests inject a fake


def _cache_seconds() -> int:
    return int(os.getenv("AWS_COST_CACHE_SECONDS", str(6 * 3600)))


def _min_refresh_seconds() -> int:
    return int(os.getenv("AWS_COST_MIN_REFRESH_SECONDS", "900"))


def _cache_path() -> Path:
    return Path(os.getenv("AWS_COST_CACHE_PATH", "data/aws_costs_cache.json"))


def _client():
    if _CLIENT_FACTORY is not None:
        return _CLIENT_FACTORY()
    import boto3
    # Cost Explorer is a global service served from us-east-1.
    return boto3.client("ce", region_name="us-east-1")


def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _amount(block: dict) -> float:
    try:
        return round(float(block[_METRIC]["Amount"]), 4)
    except (KeyError, TypeError, ValueError):
        return 0.0


def _unit(block: dict) -> str:
    try:
        return block[_METRIC]["Unit"] or "USD"
    except (KeyError, TypeError):
        return "USD"


# --- cache ---------------------------------------------------------------

def _load_disk() -> dict:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_disk() -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_MEMORY, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.warning("could not persist AWS cost cache", exc_info=True)


def _cached(key: str, force: bool, fetch: Callable[[], dict]) -> dict:
    """Serve from cache unless stale (or force and past the refresh floor)."""
    with _LOCK:
        if not _MEMORY:
            _MEMORY.update(_load_disk())
        entry = _MEMORY.get(key)
        age = time.time() - entry["fetched_at"] if entry else None
        # 1. fresh and not forced  2. forced but inside the refresh floor
        #    -> serve the cache.  3. otherwise ask AWS (costs $0.01/request).
        if entry and ((not force and age < _cache_seconds()) or (force and age < _min_refresh_seconds())):
            return {**entry["data"], "cached": True, "age_seconds": int(age),
                    "refresh_allowed_in": max(0, int(_min_refresh_seconds() - age))}
    try:
        data = fetch()
    except Exception as exc:  # never break the dashboard
        failure = _describe_failure(exc)
        if entry:  # keep showing the last good numbers, marked stale
            return {**entry["data"], "cached": True, "stale": True,
                    "age_seconds": int(age or 0), "refresh_error": failure}
        return {"available": False, **failure}
    with _LOCK:
        _MEMORY[key] = {"fetched_at": time.time(), "data": data}
        _save_disk()
    return {**data, "cached": False, "age_seconds": 0,
            "refresh_allowed_in": _min_refresh_seconds()}


def _describe_failure(exc: Exception) -> dict:
    name = type(exc).__name__
    text = str(exc)
    if name in {"NoCredentialsError", "PartialCredentialsError"}:
        kind = "no_credentials"
    elif "AccessDenied" in text or "UnauthorizedOperation" in text or "not authorized" in text:
        kind = "access_denied"
    elif "DataUnavailable" in text:
        kind = "data_unavailable"
    else:
        kind = "error"
    logger.warning("AWS Cost Explorer request failed (%s): %s", kind, text[:200])
    return {"error_kind": kind, "message": text[:300]}


# --- queries -------------------------------------------------------------

def _windows(today: dt.date) -> dict[str, dt.date]:
    month_start = today.replace(day=1)
    last_month_start = (month_start - dt.timedelta(days=1)).replace(day=1)
    next_month_start = (month_start + dt.timedelta(days=32)).replace(day=1)
    return {
        "today": today,
        "tomorrow": today + dt.timedelta(days=1),   # End is exclusive
        "month_start": month_start,
        "last_month_start": last_month_start,
        "next_month_start": next_month_start,
        "last30_start": today - dt.timedelta(days=29),
    }


def _fetch_overview() -> dict:
    ce = _client()
    w = _windows(_today())
    unit = "USD"

    # 1. Last month + this month, per service.
    monthly = ce.get_cost_and_usage(
        TimePeriod={"Start": w["last_month_start"].isoformat(), "End": w["tomorrow"].isoformat()},
        Granularity="MONTHLY",
        Metrics=[_METRIC],
        Filter=_EXCLUDE_CREDITS,
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    services: dict[str, dict] = {}
    last_month_total = month_to_date = 0.0
    for period in monthly.get("ResultsByTime", []):
        is_current = period["TimePeriod"]["Start"] >= w["month_start"].isoformat()
        for group in period.get("Groups", []):
            name = group["Keys"][0]
            value = _amount(group["Metrics"])
            unit = _unit(group["Metrics"])
            row = services.setdefault(name, {"service": name, "month_to_date": 0.0, "last_month": 0.0})
            if is_current:
                row["month_to_date"] = round(row["month_to_date"] + value, 4)
                month_to_date += value
            else:
                row["last_month"] = round(row["last_month"] + value, 4)
                last_month_total += value

    # 2. Last 30 days, day by day.
    daily = ce.get_cost_and_usage(
        TimePeriod={"Start": w["last30_start"].isoformat(), "End": w["tomorrow"].isoformat()},
        Granularity="DAILY",
        Metrics=[_METRIC],
        Filter=_EXCLUDE_CREDITS,
    )
    days = [{"date": p["TimePeriod"]["Start"], "amount": _amount(p.get("Total", {}))}
            for p in daily.get("ResultsByTime", [])]

    # 3. Credits and refunds applied this month / last month (a negative
    #    amount on the bill) - what the credits are absorbing.
    credit_now = credit_last = 0.0
    try:
        cr = ce.get_cost_and_usage(
            TimePeriod={"Start": w["last_month_start"].isoformat(), "End": w["tomorrow"].isoformat()},
            Granularity="MONTHLY",
            Metrics=[_METRIC],
            Filter={"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}},
        )
        for period in cr.get("ResultsByTime", []):
            value = _amount(period.get("Total", {}))
            if period["TimePeriod"]["Start"] >= w["month_start"].isoformat():
                credit_now += value
            else:
                credit_last += value
    except Exception as exc:
        logger.info("credit lookup unavailable: %s", str(exc)[:160])

    # 4. Forecast to month end (AWS refuses when the window is too short or
    #    history too thin - that is not an error for the dashboard).
    forecast = None
    if w["tomorrow"] < w["next_month_start"]:
        try:
            fc = ce.get_cost_forecast(
                TimePeriod={"Start": w["tomorrow"].isoformat(), "End": w["next_month_start"].isoformat()},
                Metric="UNBLENDED_COST",
                Granularity="MONTHLY",
                Filter=_EXCLUDE_CREDITS,
            )
            forecast = round(month_to_date + float(fc["Total"]["Amount"]), 2)
        except Exception as exc:
            logger.info("cost forecast unavailable: %s", str(exc)[:160])

    ranked = sorted(services.values(), key=lambda r: (r["month_to_date"], r["last_month"]), reverse=True)
    return {
        "available": True,
        "currency": unit,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "period": {"month_start": w["month_start"].isoformat(), "today": w["today"].isoformat()},
        "month_to_date": round(month_to_date, 2),
        "last_month": round(last_month_total, 2),
        "forecast_month_end": forecast,
        "last_30_days": round(sum(d["amount"] for d in days), 2),
        "credits_month_to_date": round(credit_now, 2),        # negative
        "credits_last_month": round(credit_last, 2),
        "net_payable_month_to_date": round(max(0.0, month_to_date + credit_now), 2),
        "daily": days,
        "services": ranked,
        "inr_rate": _inr_rate(),
    }


def _fetch_service(service: str) -> dict:
    ce = _client()
    w = _windows(_today())
    only = {"And": [{"Dimensions": {"Key": "SERVICE", "Values": [service]}}, _EXCLUDE_CREDITS]}

    monthly = ce.get_cost_and_usage(
        TimePeriod={"Start": w["last_month_start"].isoformat(), "End": w["tomorrow"].isoformat()},
        Granularity="MONTHLY",
        Metrics=[_METRIC],
        Filter=only,
        GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
    )
    usage: dict[str, dict] = {}
    for period in monthly.get("ResultsByTime", []):
        is_current = period["TimePeriod"]["Start"] >= w["month_start"].isoformat()
        for group in period.get("Groups", []):
            name = group["Keys"][0]
            row = usage.setdefault(name, {"usage_type": name, "month_to_date": 0.0, "last_month": 0.0})
            key = "month_to_date" if is_current else "last_month"
            row[key] = round(row[key] + _amount(group["Metrics"]), 4)

    daily = ce.get_cost_and_usage(
        TimePeriod={"Start": w["last30_start"].isoformat(), "End": w["tomorrow"].isoformat()},
        Granularity="DAILY",
        Metrics=[_METRIC],
        Filter=only,
    )
    days = [{"date": p["TimePeriod"]["Start"], "amount": _amount(p.get("Total", {}))}
            for p in daily.get("ResultsByTime", [])]
    ranked = sorted(usage.values(), key=lambda r: (r["month_to_date"], r["last_month"]), reverse=True)
    return {
        "available": True,
        "service": service,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "month_to_date": round(sum(r["month_to_date"] for r in ranked), 2),
        "last_month": round(sum(r["last_month"] for r in ranked), 2),
        "last_30_days": round(sum(d["amount"] for d in days), 2),
        "usage_types": ranked,
        "daily": days,
    }


def _inr_rate() -> float | None:
    """Optional display conversion (AWS bills in USD). Set AWS_COST_INR_RATE."""
    try:
        rate = float(os.getenv("AWS_COST_INR_RATE", ""))
        return rate if rate > 0 else None
    except ValueError:
        return None


# --- public API ----------------------------------------------------------

def overview(force: bool = False) -> dict:
    return _cached("overview", force, _fetch_overview)


def service_detail(service: str, force: bool = False) -> dict:
    service = (service or "").strip()
    if not service or len(service) > 200:
        return {"available": False, "error_kind": "bad_request", "message": "service name required"}
    return _cached(f"service::{service}", force, lambda: _fetch_service(service))
