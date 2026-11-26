import { useEffect, useState } from "react";
import { api } from "../lib/api";
import {
  Card,
  Strip,
  StatusBar,
  Badge,
  Empty,
  KV,
  Bar,
  Spark,
  PageHead,
  fmt,
  secs,
  ago,
  clock,
} from "../components/ui";
import { useToast } from "../components/Toast";

export default function Overview({ go }: { go: (p: string) => void }) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState("");
  const toast = useToast();

  async function load() {
    try {
      setD(await api.overview());
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  if (err)
    return (
      <Card>
        <Empty>Could not reach the API: {err}</Empty>
      </Card>
    );
  if (!d)
    return (
      <>
        <div className="skeleton" style={{ height: 46, marginBottom: 12 }} />
        <div className="skeleton" style={{ height: 96, marginBottom: 12 }} />
        <div className="skeleton" style={{ height: 260 }} />
      </>
    );

  const { live, runtime, config, metrics } = d;
  const k = live.keys;
  const healthyPct = k.total ? (k.healthy / k.total) * 100 : 0;
  const healthOk = live.reachable && healthyPct > 90;
  const series: number[] = (d.series || []).map((s: any) => s.active_reqs ?? 0);
  const errSeries: number[] = (d.series || []).map((s: any) => s.error_rate ?? 0);

  return (
    <>
      <PageHead
        title="Overview"
        sub={`${live.service || "Atlas"} · ${config.provider} · ${config.default_model}`}
        actions={
          <>
            <button className="btn" onClick={load}>
              Refresh
            </button>
            <button className="btn btn-primary" onClick={() => go("system")}>
              Restart proxy
            </button>
          </>
        }
      />

      <StatusBar
        ok={live.reachable ? healthOk : false}
        name={
          !live.reachable
            ? "Proxy unreachable"
            : healthOk
              ? "All systems operational"
              : "Degraded"
        }
        meta={`${config.listen} · ${runtime.mode || "unknown mode"}`}
        right={
          <span className="status-meta">
            {fmt(k.healthy)}/{fmt(k.total)} keys · {fmt(k.in_flight)} in flight
          </span>
        }
      />

      <Strip
        items={[
          {
            label: "Active requests",
            value: fmt(metrics.active_requests),
            foot: "in flight now",
          },
          {
            label: "Requests (pool)",
            value: fmt(metrics.total_requests),
            foot: "lifetime, all keys",
          },
          {
            label: "Avg latency",
            value: secs(metrics.avg_latency_ms),
            foot: "last upstream response",
          },
          {
            label: "Error rate",
            value: metrics.error_rate_pct,
            unit: "%",
            foot: "pool lifetime",
            tone: metrics.error_rate_pct > 5 ? "bad" : undefined,
          },
        ]}
      />

      <div className="grid grid-2-wide" style={{ marginBottom: 12 }}>
        <Card
          title="Keypool health"
          sub={`${fmt(k.total)} keys · ${healthyPct.toFixed(0)}% healthy`}
          link={
            <span className="card-link" onClick={() => go("keypool")}>
              manage →
            </span>
          }
        >
          <Bar
            segments={[
              { label: "Healthy", value: k.healthy, color: "var(--ok)" },
              { label: "Cooling", value: k.cooling, color: "var(--warn)" },
              { label: "Suspended", value: k.suspended, color: "var(--bad)" },
            ]}
          />
          <div style={{ marginTop: 14 }}>
            <KV
              rows={[
                ["Provider", config.provider],
                ["Default model", config.default_model],
                ["Context window", config.context_window ? `${Math.round(config.context_window / 1000)}K` : "—"],
                ["Concurrency / key", String(config.max_concurrent_per_key)],
                ["Cooldown", `${config.cooldown_base_seconds}s`],
              ]}
            />
          </div>
        </Card>

        <Card title="Session traffic" sub={`${d.session_requests ?? 0} requests parsed this session`}>
          <Spark data={series} />
          <div style={{ height: 10 }} />
          <div className="card-sub" style={{ marginBottom: 4 }}>
            Error rate %
          </div>
          <Spark data={errSeries} color="var(--bad)" />
          <div style={{ marginTop: 14 }}>
            <KV
              rows={[
                ["Failed (session)", fmt(d.session_failed ?? 0)],
                ["Samples", fmt((d.series || []).length)],
              ]}
            />
          </div>
        </Card>
      </div>

      <div className="grid grid-2">
        <Card
          title="Top models"
          sub="from parsed request log"
          link={
            <span className="card-link" onClick={() => go("analytics")}>
              analytics →
            </span>
          }
        >
          {d.models?.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th className="num">Requests</th>
                  <th className="num">Errors</th>
                </tr>
              </thead>
              <tbody>
                {d.models.map((m: any) => (
                  <tr key={m.model}>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {m.model}
                    </td>
                    <td className="num">{fmt(m.requests)}</td>
                    <td className="num">
                      {m.errors ? (
                        <span style={{ color: "var(--bad)" }}>{m.errors}</span>
                      ) : (
                        "0"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No requests parsed yet</Empty>
          )}
        </Card>

        <Card
          title="Recent activity"
          sub="administrative audit"
          link={
            <span className="card-link" onClick={() => go("security")}>
              all →
            </span>
          }
        >
          {d.audit?.length ? (
            <div className="activity">
              {d.audit.map((a: any, i: number) => (
                <div className="activity-row" key={i}>
                  <span
                    className="activity-dot"
                    style={{
                      background: a.event.includes("fail")
                        ? "var(--bad)"
                        : "var(--accent)",
                    }}
                  />
                  <span style={{ textTransform: "capitalize" }}>
                    {a.event.replace(/_/g, " ")}
                  </span>
                  <span className="activity-time mono">{ago(a.ts)}</span>
                </div>
              ))}
            </div>
          ) : (
            <Empty>No administrative activity yet</Empty>
          )}
        </Card>
      </div>

      {d.errors?.length ? (
        <Card title="Latest errors" sub="from the proxy log" style={{ marginTop: 12 }}>
          <div className="activity">
            {d.errors.map((e: any, i: number) => (
              <div className="activity-row" key={i}>
                <Badge tone="bad">{e.status ?? "ERR"}</Badge>
                <span className="mono" style={{ fontSize: 11 }}>
                  {e.id}
                </span>
                <span
                  style={{
                    color: "var(--text-2)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    flex: 1,
                  }}
                >
                  {e.detail}
                </span>
                <span className="activity-time mono">{clock(e.ts)}</span>
              </div>
            ))}
          </div>
        </Card>
      ) : null}
    </>
  );
}