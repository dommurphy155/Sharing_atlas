import { useEffect, useState } from "react";
import { api } from "../lib/api";
import {
  Card,
  PageHead,
  Badge,
  Empty,
  KV,
  Modal,
  PasswordField,
  ago,
} from "../components/ui";
import { useToast } from "../components/Toast";

export default function System() {
  const [d, setD] = useState<any>(null);
  const [diag, setDiag] = useState<any>(null);
  const [ups, setUps] = useState<any>(null);
  const [backups, setBackups] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const [modal, setModal] = useState<null | { unit: string; action: string } | "backup">(null);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function load() {
    try {
      setD(await api.systemStatus());
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  }
  async function loadMore() {
    try {
      const [dg, u, b] = await Promise.all([
        api.diagnostics(),
        api.updates(),
        api.backups(),
      ]);
      setDiag(dg);
      setUps(u);
      setBackups(b.backups || []);
    } catch {
      /* non-fatal */
    }
  }
  useEffect(() => {
    load();
    loadMore();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  async function run() {
    if (!modal) return;
    setBusy(true);
    try {
      if (modal === "backup") {
        await api.backup(pw);
        toast("Backup written");
      } else {
        await api.unitAction(modal.unit, modal.action, pw);
        toast(`${modal.unit} ${modal.action} issued`);
      }
      setModal(null);
      setPw("");
      await load();
      await loadMore();
    } catch (e: any) {
      toast(e.message, "bad");
    } finally {
      setBusy(false);
    }
  }

  if (err)
    return (
      <Card>
        <Empty>{err}</Empty>
      </Card>
    );
  if (!d) return <div className="skeleton" style={{ height: 420 }} />;

  const unit = d.proxy_unit || {};
  const dash = d.dashboard_unit || {};
  const proc = d.process || {};

  return (
    <>
      <PageHead
        title="System"
        sub={`dashboard ${d.version?.dashboard} · commit ${d.version?.commit}`}
        actions={
          <>
            <button
              className="btn"
              onClick={() => {
                setModal("backup");
                setPw("");
              }}
            >
              Back up config
            </button>
            <button
              className="btn"
              onClick={() => {
                setModal({ unit: "proxy", action: "restart" });
                setPw("");
              }}
            >
              Restart proxy
            </button>
          </>
        }
      />

      <div className="strip">
        {[
          ["Proxy", unit.active === "active" ? "running" : unit.active || "unknown"],
          ["Dashboard", dash.active === "active" ? "running" : dash.active || "unknown"],
          ["Health", d.health?.ok ? "all checks pass" : "check failed"],
          ["Disk free", diag?.disk_free_gb != null ? `${diag.disk_free_gb} GB` : "—"],
        ].map(([l, v]) => (
          <div className="strip-item" key={l}>
            <div className="strip-label">{l}</div>
            <div className="strip-value">{v}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-2">
        <Card title="Proxy service" sub={unit.unit}>
          <KV
            rows={[
              ["Supervisor", unit.supervisor || "systemd"],
              ["State", <Badge tone={unit.active === "active" ? "ok" : "bad"}>{unit.active}</Badge>],
              ["Sub-state", unit.sub || "—"],
              ["PID", unit.pid || "—"],
              ["Since", unit.since || "—"],
              ["Command", proc.cmdline || "—"],
              ["RSS", proc.vms || "—"],
              ["Threads", proc.threads || "—"],
            ]}
          />
          <div className="row" style={{ marginTop: 14 }}>
            {["start", "stop", "restart"].map((a) => (
              <button
                key={a}
                className={`btn ${a === "stop" ? "btn-danger" : ""}`}
                onClick={() => {
                  setModal({ unit: "proxy", action: a });
                  setPw("");
                }}
              >
                {a}
              </button>
            ))}
          </div>
        </Card>

        <Card title="Dashboard service" sub={dash.unit}>
          <KV
            rows={[
              ["State", <Badge tone={dash.active === "active" ? "ok" : "bad"}>{dash.active}</Badge>],
              ["Sub-state", dash.sub || "—"],
              ["PID", dash.pid || "—"],
              ["Since", dash.since || "—"],
            ]}
          />
          <div className="row" style={{ marginTop: 14 }}>
            {["start", "stop", "restart"].map((a) => (
              <button
                key={a}
                className={`btn ${a === "stop" ? "btn-danger" : ""}`}
                onClick={() => {
                  setModal({ unit: "dashboard", action: a });
                  setPw("");
                }}
              >
                {a}
              </button>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid grid-2" style={{ marginTop: 12 }}>
        <Card title="Health checks">
          {d.health?.checks?.length ? (
            <div className="activity">
              {d.health.checks.map((c: any) => (
                <div className="activity-row" key={c.name}>
                  <span
                    className="activity-dot"
                    style={{ background: c.ok ? "var(--ok)" : "var(--bad)" }}
                  />
                  <span>{c.name}</span>
                  <span
                    className="activity-time mono"
                    style={{ color: c.ok ? "var(--text-3)" : "var(--bad)" }}
                  >
                    {c.detail}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Empty>No checks</Empty>
          )}
        </Card>

        <Card title="Version & updates">
          <KV
            rows={[
              ["Dashboard", d.version?.dashboard || "—"],
              ["Commit", d.version?.commit || "—"],
              ["Branch", d.version?.branch || "—"],
              ["Working tree", d.version?.dirty ? "dirty" : "clean"],
              [
                "Updates",
                ups ? (
                  ups.available ? (
                    <Badge tone="warn">{ups.behind} behind</Badge>
                  ) : (
                    <Badge tone="ok">up to date</Badge>
                  )
                ) : (
                  "—"
                ),
              ],
            ]}
          />
          <div className="hint" style={{ marginTop: 10 }}>
            Updates are checked with <span className="mono">git fetch --dry-run</span>.
            Applying an update is deliberately not exposed here — do it from the
            CLI so the backup and rollback path stays yours.
          </div>
        </Card>
      </div>

      <Card title="Backups" sub="config + key file, owner-only" style={{ marginTop: 12 }}>
        {backups.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Created</th>
                <th>Path</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((b) => (
                <tr key={b.name}>
                  <td className="mono" style={{ fontSize: 11 }}>
                    {b.name}
                  </td>
                  <td style={{ color: "var(--text-3)" }}>{ago(b.ts)}</td>
                  <td className="mono" style={{ fontSize: 11, color: "var(--text-3)" }}>
                    {b.path}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No backups yet</Empty>
        )}
      </Card>

      {modal && (
        <Modal
          title={
            modal === "backup"
              ? "Back up configuration"
              : `${modal.action} ${modal.unit}`
          }
          onClose={() => setModal(null)}
          actions={
            <>
              <button className="btn" onClick={() => setModal(null)}>
                Cancel
              </button>
              <button
                className={`btn ${modal !== "backup" && modal.action === "stop" ? "btn-danger" : "btn-primary"}`}
                disabled={busy || !pw}
                onClick={run}
              >
                {busy ? "Working…" : "Confirm"}
              </button>
            </>
          }
        >
          <p style={{ fontSize: 12, color: "var(--text-2)", marginBottom: 12 }}>
            {modal === "backup"
              ? "Copies .env, the runtime provider file and the key file into an owner-only backup directory."
              : modal.action === "restart"
                ? "The proxy restarts. In-flight requests are dropped. The dashboard stays up."
                : modal.action === "stop"
                  ? "The proxy stops. API traffic fails until you start it again."
                  : "The proxy starts."}
          </p>
          <PasswordField value={pw} onChange={setPw} />
        </Modal>
      )}
    </>
  );
}
