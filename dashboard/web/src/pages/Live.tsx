import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Card, PageHead, Badge, Empty, KV, fmt, secs, clock } from "../components/ui";

export default function Live() {
  const [d, setD] = useState<any>(null);
  const [errs, setErrs] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [onlyErr, setOnlyErr] = useState(false);
  const [insp, setInsp] = useState<any>(null);

  async function load() {
    try {
      const [l, e] = await Promise.all([api.live(), api.liveErrors()]);
      setD(l);
      setErrs(e.errors || []);
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  if (err)
    return (
      <Card>
        <Empty>{err}</Empty>
      </Card>
    );
  if (!d) return <div className="skeleton" style={{ height: 420 }} />;

  let reqs: any[] = d.requests || [];
  if (onlyErr) reqs = reqs.filter((r) => r.state === "failed");
  if (q.trim()) {
    const s = q.trim().toLowerCase();
    reqs = reqs.filter(
      (r) => r.id.includes(s) || (r.model || "").toLowerCase().includes(s),
    );
  }
  const p = d.proxy;

  return (
    <>
      <PageHead
        title="Live Requests"
        sub={`${d.summary.total} parsed · ${d.summary.failed} failed · ${fmt(p.keys?.in_flight)} in flight`}
      />

      <div className="strip">
        {[
          ["In flight", fmt(p.keys?.in_flight)],
          ["Keys healthy", `${fmt(p.keys?.healthy)}/${fmt(p.keys?.total)}`],
          ["Session avg latency", secs(d.summary.avg_latency_ms)],
          ["Proxy", p.reachable ? "reachable" : "down"],
        ].map(([l, v]) => (
          <div className="strip-item" key={l}>
            <div className="strip-label">{l}</div>
            <div className="strip-value">{v}</div>
          </div>
        ))}
      </div>

      <Card
        title="Request stream"
        sub="assembled from the proxy log — no bodies retained"
        link={
          <div className="row tight" style={{ marginLeft: "auto" }}>
            <input
              className="input"
              style={{ width: 160, padding: "3px 8px", fontSize: 12 }}
              placeholder="filter id / model"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <div className="seg">
              <button
                className={!onlyErr ? "on" : ""}
                onClick={() => setOnlyErr(false)}
              >
                all
              </button>
              <button
                className={onlyErr ? "on hot" : ""}
                onClick={() => setOnlyErr(true)}
              >
                failed
              </button>
            </div>
          </div>
        }
      >
        {reqs.length ? (
          <div className="table-wrap" style={{ maxHeight: 460 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Id</th>
                  <th>Model</th>
                  <th>Provider</th>
                  <th className="num">Key</th>
                  <th className="num">Status</th>
                  <th className="num">Duration</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {reqs.map((r) => (
                  <tr
                    key={r.id}
                    style={{ cursor: "pointer" }}
                    onClick={() => setInsp(r)}
                  >
                    <td className="mono" style={{ color: "var(--text-3)" }}>
                      {clock(r.ts)}
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {r.id}
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {r.model || "—"}
                    </td>
                    <td style={{ color: "var(--text-3)" }}>{r.provider || "—"}</td>
                    <td className="num">{r.key_index ?? "—"}</td>
                    <td className="num">{r.status ?? "—"}</td>
                    <td className="num">
                      {secs(r.duration_ms)}
                    </td>
                    <td>
                      <Badge
                        tone={
                          r.state === "done"
                            ? "ok"
                            : r.state === "failed"
                              ? "bad"
                              : "muted"
                        }
                      >
                        {r.state}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No requests match</Empty>
        )}
      </Card>

      <Card title="Errors" sub="latest upstream failures" style={{ marginTop: 12 }}>
        {errs.length ? (
          <div className="activity">
            {errs.map((e, i) => (
              <div className="activity-row" key={i}>
                <Badge tone={e.kind === "error" ? "bad" : "warn"}>
                  {e.status ?? e.kind}
                </Badge>
                <span className="mono" style={{ fontSize: 11 }}>
                  {e.id}
                </span>
                <span
                  style={{
                    color: "var(--text-2)",
                    flex: 1,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {e.detail}
                </span>
                <span className="activity-time mono">{clock(e.ts)}</span>
              </div>
            ))}
          </div>
        ) : (
          <Empty>No errors recorded</Empty>
        )}
      </Card>

      {insp && (
        <div className="modal-scrim" onClick={() => setInsp(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">Request {insp.id}</div>
            <KV
              rows={[
                ["Time", new Date(insp.ts * 1000).toLocaleString()],
                ["Provider", insp.provider || "—"],
                ["Model", insp.model || "—"],
                ["Stream", insp.stream ? "yes" : "no"],
                ["Key index", insp.key_index ?? "—"],
                ["Status", insp.status ?? "—"],
                ["Duration", secs(insp.duration_ms)],
                ["State", insp.state],
              ]}
            />
            {insp.detail && (
              <div style={{ marginTop: 12 }}>
                <div className="card-sub" style={{ marginBottom: 4 }}>
                  Detail (redacted)
                </div>
                <pre
                  className="mono"
                  style={{
                    fontSize: 11,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    background: "var(--bg)",
                    border: "1px solid var(--border-soft)",
                    borderRadius: "var(--r-1)",
                    padding: 8,
                  }}
                >
                  {insp.detail}
                </pre>
              </div>
            )}
            <div className="modal-actions">
              <button className="btn" onClick={() => setInsp(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
