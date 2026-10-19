"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "./admin.module.css";

// "AWS costs" tab. The real bill from AWS Cost Explorer, read through
// status_server -> sakhi_aws_costs (cached there: Cost Explorer charges per
// request and its data lags ~24 h, so there is no polling here - it loads
// once, and "Refresh" is rate-limited server-side).
//
// Left column: the month at a glance and every service.
// Right column: Explore - click a service to see its usage types and trend.

type Day = { date: string; amount: number };
type ServiceRow = { service: string; month_to_date: number; last_month: number };
type UsageRow = { usage_type: string; month_to_date: number; last_month: number };

type CostMeta = {
  available: boolean;
  error_kind?: string;
  message?: string;
  cached?: boolean;
  stale?: boolean;
  age_seconds?: number;
  refresh_allowed_in?: number;
  refresh_error?: { error_kind: string; message: string };
  generated_at?: string;
};

type Overview = CostMeta & {
  currency?: string;
  period?: { month_start: string; today: string };
  month_to_date?: number;
  last_month?: number;
  forecast_month_end?: number | null;
  last_30_days?: number;
  daily?: Day[];
  services?: ServiceRow[];
  inr_rate?: number | null;
  credits_month_to_date?: number;
  credits_last_month?: number;
  net_payable_month_to_date?: number;
};

type ServiceDetail = CostMeta & {
  service?: string;
  month_to_date?: number;
  last_month?: number;
  last_30_days?: number;
  usage_types?: UsageRow[];
  daily?: Day[];
};

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  return (await res.json()) as T;
}

function money(value: number | null | undefined, rate?: number | null): string {
  if (value === null || value === undefined) return "—";
  const usd = `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return rate ? `${usd}  ·  ₹${Math.round(value * rate).toLocaleString()}` : usd;
}

function ago(seconds: number | undefined): string {
  if (seconds === undefined) return "";
  if (seconds < 90) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

function Bars({ days, highlight }: { days: Day[]; highlight?: boolean }) {
  const max = Math.max(0.01, ...days.map((d) => d.amount));
  return (
    <div className={styles.bars} role="img" aria-label="Daily spend, last 30 days">
      {days.map((d) => (
        <div key={d.date} className={styles.barCol} title={`${d.date}: $${d.amount.toFixed(2)}`}>
          <div
            className={highlight ? styles.barFillAlt : styles.barFill}
            style={{ height: `${Math.max(2, (d.amount / max) * 100)}%` }}
          />
        </div>
      ))}
    </div>
  );
}

function Unavailable({ data }: { data: CostMeta }) {
  const denied = data.error_kind === "access_denied" || data.error_kind === "no_credentials";
  return (
    <div className={styles.card}>
      <h2 className={styles.cardTitle}>AWS costs are not connected yet</h2>
      {denied ? (
        <>
          <p className={styles.muted}>
            The server&apos;s IAM role is not allowed to read Cost Explorer. Attach a policy with these two
            read-only actions to the EC2 instance role, then press Refresh:
          </p>
          <pre className={styles.code}>{`ce:GetCostAndUsage
ce:GetCostForecast`}</pre>
          <p className={styles.muted}>
            Cost Explorer must also be enabled once in the AWS Billing console (Cost Explorer → Launch).
          </p>
        </>
      ) : (
        <p className={styles.muted}>{data.message || "Cost Explorer did not answer."}</p>
      )}
    </div>
  );
}

export default function AwsCosts() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true);
    try {
      setData(await getJSON<Overview>(`/api/dashboard/costs${refresh ? "?refresh=1" : ""}`));
    } catch (err) {
      setData({ available: false, error_kind: "error", message: err instanceof Error ? err.message : String(err) });
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    const first = setTimeout(() => load(false), 0);
    return () => clearTimeout(first);
  }, [load]);

  if (!data) return <div className={styles.card}><p className={styles.muted}>Loading AWS costs…</p></div>;
  if (!data.available) {
    return (
      <>
        <Unavailable data={data} />
        <button className={styles.secondaryBtn} onClick={() => load(true)} disabled={loading}>
          {loading ? "Checking…" : "Refresh"}
        </button>
      </>
    );
  }

  const rate = data.inr_rate;
  const services = data.services || [];
  const top = services[0];
  const canRefresh = !loading && (data.refresh_allowed_in ?? 0) === 0;

  return (
    <>
      <div className={styles.costHeader}>
        <p className={styles.muted}>
          Real AWS usage from Cost Explorer, before credits · {data.period?.month_start} to {data.period?.today} · updated{" "}
          {ago(data.age_seconds)}{data.stale ? " (showing last good numbers)" : ""} · AWS data lags ~24 h
        </p>
        <button
          className={styles.secondaryBtn}
          onClick={() => load(true)}
          disabled={!canRefresh}
          title={canRefresh ? "Ask AWS for new numbers ($0.01 per request)" : `Next refresh in ${Math.ceil((data.refresh_allowed_in || 0) / 60)} min`}
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {data.refresh_error && <div className={styles.banner}>Refresh failed: {data.refresh_error.message}</div>}

      <section className={styles.counters}>
        <CostCounter label="Usage this month (before credits)" value={money(data.month_to_date, rate)} />
        <CostCounter label="Credits applied this month" value={money(Math.abs(data.credits_month_to_date || 0), rate)} />
        <CostCounter label="You pay this month" value={money(data.net_payable_month_to_date, rate)} />
        <CostCounter label="Forecast for the month" value={money(data.forecast_month_end, rate)} tone="warn" />
        <CostCounter label="Last month" value={money(data.last_month, rate)} />
        <CostCounter label="Last 30 days" value={money(data.last_30_days, rate)} />
        <CostCounter label="Biggest service" value={top ? top.service.replace(/^Amazon |^AWS /, "") : "—"} />
      </section>

      <section className={styles.split}>
        <div className={styles.card}>
          <h2 className={styles.cardTitle}>Daily spend · last 30 days</h2>
          <Bars days={data.daily || []} />
          <h3 className={styles.sectionTitle}>By service · click one to explore</h3>
          <table className={styles.table}>
            <thead>
              <tr><th>Service</th><th>This month</th><th>Last month</th><th>Share</th></tr>
            </thead>
            <tbody>
              {services.map((s) => {
                const share = data.month_to_date ? (s.month_to_date / data.month_to_date) * 100 : 0;
                return (
                  <tr
                    key={s.service}
                    className={selected === s.service ? styles.rowSelected : styles.row}
                    onClick={() => setSelected(selected === s.service ? null : s.service)}
                  >
                    <td>{s.service}</td>
                    <td>{money(s.month_to_date)}</td>
                    <td>{money(s.last_month)}</td>
                    <td>
                      <div className={styles.shareTrack}><div className={styles.shareFill} style={{ width: `${share}%` }} /></div>
                      <span className={styles.muted}>{share.toFixed(0)}%</span>
                    </td>
                  </tr>
                );
              })}
              {services.length === 0 && (
                <tr><td colSpan={4} className={styles.muted}>No spend recorded for this period.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className={styles.card}>
          <h2 className={styles.cardTitle}>Explore</h2>
          {selected ? (
            <ServiceExplorer service={selected} rate={rate} />
          ) : (
            <p className={styles.muted}>
              Pick a service on the left to see exactly what it is charging for - instance hours, storage,
              data transfer, requests - and its day-by-day trend.
            </p>
          )}
        </div>
      </section>
    </>
  );
}

function CostCounter({ label, value, tone }: { label: string; value: string; tone?: "warn" }) {
  return (
    <div className={`${styles.counter} ${tone === "warn" ? styles.counterWarn : ""}`}>
      <div className={styles.costValue}>{value}</div>
      <div className={styles.counterLabel}>{label}</div>
    </div>
  );
}

function ServiceExplorer({ service, rate }: { service: string; rate?: number | null }) {
  const [detail, setDetail] = useState<ServiceDetail | null>(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setDetail(null);
      const res = await getJSON<ServiceDetail>(`/api/dashboard/costs/service?name=${encodeURIComponent(service)}`)
        .catch((err) => ({ available: false, message: String(err) }) as ServiceDetail);
      if (!cancelled) setDetail(res);
    };
    const t = setTimeout(run, 0);
    return () => { cancelled = true; clearTimeout(t); };
  }, [service]);

  if (!detail) return <p className={styles.muted}>Loading {service}…</p>;
  if (!detail.available) return <div className={styles.errorBox}>Could not load {service}: {detail.message}</div>;

  const usage = detail.usage_types || [];
  return (
    <>
      <h3 className={styles.exploreTitle}>{service}</h3>
      <div className={styles.detailGrid}>
        <div><span className={styles.k}>This month</span>{money(detail.month_to_date, rate)}</div>
        <div><span className={styles.k}>Last month</span>{money(detail.last_month, rate)}</div>
        <div><span className={styles.k}>Last 30 days</span>{money(detail.last_30_days, rate)}</div>
      </div>
      <h3 className={styles.sectionTitle}>Daily · last 30 days</h3>
      <Bars days={detail.daily || []} highlight />
      <h3 className={styles.sectionTitle}>What it is charging for</h3>
      <table className={styles.table}>
        <thead><tr><th>Usage type</th><th>This month</th><th>Last month</th></tr></thead>
        <tbody>
          {usage.map((u) => (
            <tr key={u.usage_type}>
              <td className={styles.usageType}>{u.usage_type}</td>
              <td>{money(u.month_to_date)}</td>
              <td>{money(u.last_month)}</td>
            </tr>
          ))}
          {usage.length === 0 && <tr><td colSpan={3} className={styles.muted}>No usage recorded.</td></tr>}
        </tbody>
      </table>
    </>
  );
}
