import { useEffect, useState } from "react";
import { api } from "../lib/api";
import {
  Card,
  PageHead,
  Empty,
  Modal,
  PasswordField,
  Badge,
} from "../components/ui";
import { useToast } from "../components/Toast";

export default function Config() {
  const [d, setD] = useState<any>(null);
  const [env, setEnv] = useState<any>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function load() {
    try {
      const [c, e] = await Promise.all([api.config(), api.env()]);
      setD(c);
      setEnv(e);
      const v: Record<string, string> = {};
      for (const it of e.items) v[it.key] = it.value ?? "";
      setValues(v);
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function doPreview() {
    setBusy(true);
    try {
      setPreview(await api.previewEnv(values, pw));
    } catch (e: any) {
      toast(e.message, "bad");
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    setBusy(true);
    try {
      const r = await api.writeEnv(values, pw);
      toast(r.backup ? "Config saved (backup written)" : "Config saved");
      setPreview(null);
      setPw("");
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
  if (!d || !env) return <div className="skeleton" style={{ height: 420 }} />;

  const dirty = (preview?.count ?? 0) > 0;

  return (
    <>
      <PageHead
        title="Configuration"
        sub={`env file: ${env.file}${env.exists ? "" : " (not created yet)"}`}
        actions={
          <>
            <button className="btn" disabled={busy || !pw} onClick={doPreview}>
              Preview changes
            </button>
            <button
              className="btn btn-primary"
              disabled={busy || !pw || !dirty}
              onClick={apply}
            >
              Apply
            </button>
          </>
        }
      />

      <Card
        title="Proxy settings"
        sub="written to .env — applied on next proxy restart"
        style={{ marginBottom: 12 }}
      >
        <div className="grid grid-2">
          {env.items.map((it: any) => (
            <div className="field" key={it.key}>
              <label>
                {it.key}
                {it.overridden ? (
                  <span style={{ color: "var(--accent)", marginLeft: 6 }}>•</span>
                ) : (
                  <span style={{ color: "var(--text-3)", marginLeft: 6 }}>
                    default
                  </span>
                )}
              </label>
              <input
                className="input mono"
                value={values[it.key] ?? ""}
                placeholder={it.default}
                onChange={(e) =>
                  setValues((v) => ({ ...v, [it.key]: e.target.value }))
                }
              />
              <div className="hint">
                effective: <span className="mono">{it.effective || "—"}</span>
              </div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 8 }}>
          <PasswordField value={pw} onChange={setPw} label="Confirm with your password to preview or apply" />
        </div>
      </Card>

      <Card title="Resolved runtime config" sub="what the proxy actually loaded">
        <table className="table">
          <tbody>
            {Object.entries(d.resolved).map(([k, v]) => (
              <tr key={k}>
                <td style={{ color: "var(--text-3)", width: 220 }}>{k}</td>
                <td className="mono" style={{ fontSize: 11 }}>
                  {String(v)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {preview && (
        <Modal
          title={
            preview.errors.length
              ? "Cannot apply — validation failed"
              : `Apply ${preview.count} change${preview.count === 1 ? "" : "s"}?`
          }
          onClose={() => setPreview(null)}
          actions={
            <>
              <button className="btn" onClick={() => setPreview(null)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={busy || !!preview.errors.length || !preview.count}
                onClick={apply}
              >
                {busy ? "Applying…" : "Apply & back up"}
              </button>
            </>
          }
        >
          {preview.errors.length > 0 && (
            <div className="form-error">
              {preview.errors.map((e: string) => (
                <div key={e}>{e}</div>
              ))}
            </div>
          )}
          {preview.changes.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>From</th>
                  <th>To</th>
                </tr>
              </thead>
              <tbody>
                {preview.changes.map((c: any) => (
                  <tr key={c.key}>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {c.key}
                    </td>
                    <td className="mono" style={{ color: "var(--text-3)", fontSize: 11 }}>
                      {c.from || "—"}
                    </td>
                    <td className="mono" style={{ color: "var(--accent)", fontSize: 11 }}>
                      {c.to || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="hint" style={{ marginTop: 10 }}>
            A backup of .env and the key file is written before any change.
            The running proxy is untouched until you restart it.
          </div>
        </Modal>
      )}
    </>
  );
}