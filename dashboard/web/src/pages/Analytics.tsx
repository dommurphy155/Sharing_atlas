import { useEffect, useState } from "react";
import { api } from "../lib/api";
import {
  Card,
  PageHead,
  Empty,
  Bar,
  Spark,
  KV,
  fmt,
} from "../components/ui";

export default function Analytics() {
  const [d, setD] = useState<any>(null);
  const [hours, setHours] = useState(6);
  const [err, setErr] = useState("");

  async function load() {
    try {
      setD(await api.analytics());
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  if (err)
    return (
      <Card>
        <Empty>{err}</Empty>
      </Card>
    );
  if (!d) return <div className="skeleton" style={{ height: 420 }} />;

  const s: any[] = d.series || [];
  const totalReq = (d.models || []).reduce((a: number, m: any) => a + m.requests, 0);
  const totalErr = (d.models || []).reduce((a: number, m: any) => a + m.errors, 0);

  return (
    <>
      <PageHead
        title="Analytics"
        sub={`sampled every ${d.interval_seconds}s · ${d.samples} buckets retained · nothing fabricated`}
        actions={
          <div className="seg">
            {[3, 6, 24].map((h) => (
              <button
                key={h}
                className={hours === h ? "on hot" : ""}
                onClick={() => setHours(h)}
              >
                {h}h
              </button>
            ))}
          </div>
        }
      />

      <div className="strip">
        {[
          ["Requests (session)", fmt(totalReq)],
          ["Errors (session)", fmt(totalErr)],
          [
            "Error rate",
            totalReq ? `${((totalErr / totalReq) * 100).toFixed(1)}%` : "—",
          ],
          ["Samples", fmt(s.length)],
        ].map(([l, v]) => (
          <div className="strip-item" key={l}>
            <div className="strip-label">{l}</div>
            <div className="strip-value">{v}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-2">
        <Card title="Active requests" sub="sampled in-flight concurrency">
          <Spark data={s.map((x) => x.active_reqs ?? 0)} />
          <div style={{ marginTop: 10 }}>
            <KV
              rows={[
                ["Buckets", String(s.length)],
                [
                  "Peak",
                  s.length ? String(Math.max(...s.map((x) => x.active_reqs ?? 0))) : "—",
                ],
              ]}
            />
          </div>
        </Card>
        <Card title="Error rate %" sub="sampled from live key stats">
          <Spark data={s.map((x) => x.error_rate ?? 0)} color="var(--bad)" />
          <div style={{ marginTop: 10 }}>
            <KV
              rows={[
                [
                  "Peak",
                  s.length
                    ? `${Math.max(...s.map((x) => x.error_rate ?? 0))}%`
                    : "—",
                ],
              ]}
            />
          </div>
        </Card>
      </div>

      <div className="grid grid-2" style={{ marginTop: 12 }}>
        <Card title="Model distribution" sub="share of parsed requests">
          {(d.models || []).length ? (
            <>
              <Bar
                segments={(d.models || []).slice(0, 6).map((m: any, i: number) => ({
                  label: m.model.length > 22 ? m.model.slice(0, 20) + "…" : m.model,
                  value: m.requests,
                  color: ["var(--accent)", "var(--info)", "var(--ok)", "var(--warn)", "var(--bad)", "var(--text-3)"][i % 6],
                }))}
              />
              <table className="table" style={{ marginTop: 14 }}>
                <thead>
                  <tr>
                    <th>Model</th>
                    <th className="num">Requests</th>
                    <th className="num">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {(d.models || []).map((m: any) => (
                    <tr key={m.model}>
                      <td className="mono" style={{ fontSize: 11 }}>
                        {m.model}
                      </td>
                      <td className="num">{fmt(m.requests)}</td>
                      <td className="num">
                        {totalReq ? ((m.requests / totalReq) * 100).toFixed(1) : 0}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <Empty>No model data yet</Empty>
          )}
        </Card>

        <Card title="Key utilisation" sub="busiest keys this session">
          {(d.keys || []).length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Key</th>
                  <th>State</th>
                  <th className="num">Requests</th>
                  <th className="num">Errors</th>
                </tr>
              </thead>
              <tbody>
                {(d.keys || []).map((k: any) => (
                  <tr key={k.index}>
                    <td className="num" style={{ color: "var(--text-3)" }}>
                      {k.index}
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {k.masked}
                    </td>
                    <td style={{ color: "var(--text-3)" }}>{k.state}</td>
                    <td className="num">{fmt(k.requests)}</td>
                    <td className="num">{k.errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No key usage recorded yet</Empty>
          )}
        </Card>
      </div>
    </>
  );
}