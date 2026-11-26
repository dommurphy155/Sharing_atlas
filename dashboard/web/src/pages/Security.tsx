import { useEffect, useState } from "react";
import { api } from "../lib/api";
import {
  Card,
  PageHead,
  Badge,
  Empty,
  KV,
  Modal,
  ago,
} from "../components/ui";
import { useToast } from "../components/Toast";

export default function Security() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState("");
  const [modal, setModal] = useState<null | "password" | "revoke">(null);
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function load() {
    try {
      setD(await api.security());
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function submit() {
    setBusy(true);
    try {
      if (modal === "password") {
        const r = await api.changePassword(cur, next);
        toast(`Password changed — ${r.revoked_sessions} other sessions revoked`);
      } else {
        const r = await api.revokeSessions();
        toast(`Revoked ${r.revoked} session(s)`);
      }
      setModal(null);
      setCur("");
      setNext("");
      await load();
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

  const current = d.current;

  return (
    <>
      <PageHead
        title="Security & Account"
        sub="password, sessions and the administrative audit trail"
        actions={
          <>
            <button
              className="btn"
              onClick={() => {
                setModal("revoke");
              }}
            >
              Revoke other sessions
            </button>
            <button
              className="btn btn-primary"
              onClick={() => {
                setModal("password");
                setCur("");
                setNext("");
              }}
            >
              Change password
            </button>
          </>
        }
      />

      <div className="grid grid-2">
        <Card title="Active sessions" sub={`${d.sessions.filter((s: any) => !s.revoked).length} live`}>
          {d.sessions.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Session</th>
                  <th>IP</th>
                  <th>Last seen</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {d.sessions.map((s: any) => (
                  <tr key={s.id}>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {s.id.slice(0, 8)}
                      {s.id.slice(0, 8) === current && (
                        <span style={{ color: "var(--accent)", marginLeft: 6 }}>
                          this one
                        </span>
                      )}
                    </td>
                    <td className="mono" style={{ fontSize: 11, color: "var(--text-3)" }}>
                      {s.ip || "—"}
                    </td>
                    <td style={{ color: "var(--text-3)" }}>{ago(s.last_seen)}</td>
                    <td>
                      <Badge tone={s.revoked ? "bad" : "ok"}>
                        {s.revoked ? "revoked" : "active"}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No sessions</Empty>
          )}
        </Card>

        <Card title="Account">
          <KV
            rows={[
              ["Role", "administrator"],
              ["Password hashing", "argon2id"],
              ["Session cookie", "HttpOnly · SameSite=Strict"],
              ["CSRF", "double-submit token"],
              ["Login throttle", "5 attempts / 5 min / IP"],
              ["Setup", "closed (admin exists)"],
            ]}
          />
          <div className="hint" style={{ marginTop: 12 }}>
            The dashboard has no default credentials. The first password set
            claims the administrator slot and setup closes permanently.
          </div>
        </Card>
      </div>

      <Card title="Audit trail" sub="administrative events" style={{ marginTop: 12 }}>
        {d.audit?.length ? (
          <div className="table-wrap" style={{ maxHeight: 420 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>Actor</th>
                  <th>IP</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {d.audit.map((a: any, i: number) => (
                  <tr key={i}>
                    <td className="mono" style={{ fontSize: 11, color: "var(--text-3)" }}>
                      {new Date(a.ts * 1000).toLocaleString()}
                    </td>
                    <td>
                      <span
                        style={{
                          textTransform: "capitalize",
                          color: a.event.includes("fail") ? "var(--bad)" : undefined,
                        }}
                      >
                        {a.event.replace(/_/g, " ")}
                      </span>
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {a.actor || "—"}
                    </td>
                    <td className="mono" style={{ fontSize: 11, color: "var(--text-3)" }}>
                      {a.ip || "—"}
                    </td>
                    <td style={{ color: "var(--text-3)", fontSize: 11 }}>
                      {a.detail || ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No audit events yet</Empty>
        )}
      </Card>

      {modal && (
        <Modal
          title={modal === "password" ? "Change password" : "Revoke other sessions"}
          onClose={() => setModal(null)}
          actions={
            <>
              <button className="btn" onClick={() => setModal(null)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={
                  busy ||
                  (modal === "password" ? !cur || next.length < 12 : false)
                }
                onClick={submit}
              >
                {busy ? "Working…" : modal === "password" ? "Change" : "Revoke"}
              </button>
            </>
          }
        >
          {modal === "password" ? (
            <>
              <div className="field">
                <label>Current password</label>
                <input
                  className="input"
                  type="password"
                  value={cur}
                  onChange={(e) => setCur(e.target.value)}
                  autoComplete="current-password"
                />
              </div>
              <div className="field">
                <label>New password</label>
                <input
                  className="input"
                  type="password"
                  value={next}
                  onChange={(e) => setNext(e.target.value)}
                  autoComplete="new-password"
                />
                <div className="hint">
                  12+ characters with upper, lower, number and symbol. Other
                  sessions are revoked.
                </div>
              </div>
            </>
          ) : (
            <p style={{ fontSize: 12, color: "var(--text-2)" }}>
              Every session except the one you are using is revoked. You stay
              signed in.
            </p>
          )}
        </Modal>
      )}
    </>
  );
}