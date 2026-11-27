import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import {
  Card,
  PageHead,
  Badge,
  Empty,
  Modal,
  PasswordField,
  fmt,
  secs,
} from "../components/ui";
import { useToast } from "../components/Toast";

type K = {
  index: number;
  masked: string;
  fingerprint: string;
  enabled: boolean;
  state: string;
  in_flight: number;
  consecutive_errors: number;
  total_requests: number;
  total_errors: number;
  last_status: number | null;
  last_latency_ms: number | null;
  cooldown_remaining_s: number;
};

const stateTone = (s: string): any => {
  if (s.startsWith("HEALTHY")) return "ok";
  if (s === "COOLING") return "warn";
  if (s === "SUSPENDED" || s === "DISABLED") return "bad";
  return "muted";
};

export default function Keypool() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<"all" | "healthy" | "cooling" | "disabled">("all");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [modal, setModal] = useState<null | "import" | "remove" | "toggle" | "bulk">(null);
  const [pw, setPw] = useState("");
  const [bulk, setBulk] = useState("");
  const [target, setTarget] = useState<K | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const [page, setPage] = useState(0);
  const PAGE = 200;

  async function load() {
    try {
      setD(await api.keysPage(page * PAGE, PAGE));
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, [page]);

  const rows = useMemo(() => {
    let ks: K[] = d?.keys || [];
    if (filter === "healthy") ks = ks.filter((k) => k.state.startsWith("HEALTHY"));
    if (filter === "cooling") ks = ks.filter((k) => k.state === "COOLING");
    if (filter === "disabled") ks = ks.filter((k) => !k.enabled);
    if (q.trim()) {
      const s = q.trim().toLowerCase();
      ks = ks.filter(
        (k) =>
          k.masked.toLowerCase().includes(s) ||
          k.fingerprint.includes(s) ||
          String(k.index) === s,
      );
    }
    return ks;
  }, [d, q, filter]);

  const shown = rows;

  async function act(fn: () => Promise<any>, okMsg: string) {
    setBusy(true);
    try {
      await fn();
      toast(okMsg);
      setModal(null);
      setPw("");
      setBulk("");
      setSel(new Set());
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
        <Empty>Could not load keypool: {err}</Empty>
      </Card>
    );
  if (!d) return <div className="skeleton" style={{ height: 400 }} />;

  const p = d.pool;
  const s = d.summary || {};

  return (
    <>
      <PageHead
        title="Keypool"
        sub={`${fmt(p.total)} keys on disk · ${fmt(p.enabled)} enabled · ${fmt(p.disabled)} disabled`}
        actions={
          <>
            <button
              className="btn"
              onClick={() => {
                setModal("import");
                setPw("");
              }}
            >
              Import keys
            </button>
            <button
              className="btn"
              disabled={!sel.size}
              onClick={() => {
                setModal("bulk");
                setPw("");
              }}
            >
              Bulk ({sel.size})
            </button>
          </>
        }
      />

      <div className="strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        {[
          ["Total", fmt(s.total ?? p.total)],
          ["Healthy", fmt(s.healthy ?? 0)],
          ["Cooling", fmt(s.cooling ?? 0)],
          ["Suspended", fmt(s.suspended ?? 0)],
        ].map(([l, v]) => (
          <div className="strip-item" key={l}>
            <div className="strip-label">{l}</div>
            <div className="strip-value">{v}</div>
          </div>
        ))}
      </div>

      <Card
        title="Keys"
        sub={`${fmt(p.total)} total · page ${page + 1} of ${Math.max(1, Math.ceil(p.total / PAGE))}`}
        link={
          <div className="row tight" style={{ marginLeft: "auto" }}>
            <input
              className="input"
              style={{ width: 190, padding: "3px 8px", fontSize: 12 }}
              placeholder="search masked / fp / index"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <div className="seg">
              {(["all", "healthy", "cooling", "disabled"] as const).map((f) => (
                <button
                  key={f}
                  className={filter === f ? "on hot" : ""}
                  onClick={() => setFilter(f)}
                >
                  {f}
                </button>
              ))}
            </div>
            <button
              className="btn btn-sm"
              disabled={page === 0}
              onClick={() => setPage((n) => n - 1)}
            >
              ‹
            </button>
            <button
              className="btn btn-sm"
              disabled={(page + 1) * PAGE >= p.total}
              onClick={() => setPage((n) => n + 1)}
            >
              ›
            </button>
          </div>
        }
      >
        {shown.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: 24 }} />
                  <th className="num">#</th>
                  <th>Key</th>
                  <th>State</th>
                  <th className="num">In flight</th>
                  <th className="num">Requests</th>
                  <th className="num">Errors</th>
                  <th className="num">Last</th>
                  <th className="num">Latency</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((k) => (
                  <tr key={k.fingerprint}>
                    <td>
                      <input
                        type="checkbox"
                        checked={sel.has(k.fingerprint)}
                        onChange={(e) => {
                          const n = new Set(sel);
                          e.target.checked ? n.add(k.fingerprint) : n.delete(k.fingerprint);
                          setSel(n);
                        }}
                      />
                    </td>
                    <td className="num" style={{ color: "var(--text-3)" }}>
                      {k.index}
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {k.masked}
                    </td>
                    <td>
                      <Badge tone={stateTone(k.state)}>{k.state}</Badge>
                    </td>
                    <td className="num">{k.in_flight}</td>
                    <td className="num">{fmt(k.total_requests)}</td>
                    <td className="num">
                      {k.total_errors ? (
                        <span style={{ color: "var(--bad)" }}>{k.total_errors}</span>
                      ) : (
                        "0"
                      )}
                    </td>
                    <td className="num">{k.last_status ?? "—"}</td>
                    <td className="num">
                      {secs(k.last_latency_ms)}
                    </td>
                    <td>
                      <div className="row tight">
                        <button
                          className="btn btn-sm"
                          onClick={() => {
                            setTarget(k);
                            setModal("toggle");
                            setPw("");
                          }}
                        >
                          {k.enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          className="btn btn-sm btn-danger"
                          onClick={() => {
                            setTarget(k);
                            setModal("remove");
                            setPw("");
                          }}
                        >
                          Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No keys match this filter</Empty>
        )}
      </Card>

      {modal === "import" && (
        <Modal
          title="Import keys"
          onClose={() => setModal(null)}
          actions={
            <>
              <button className="btn" onClick={() => setModal(null)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={busy || !bulk.trim() || !pw}
                onClick={() =>
                  act(
                    () =>
                      api.importKeys(
                        bulk.split(/[\s,]+/).filter(Boolean),
                        pw,
                      ),
                    "Keys imported",
                  )
                }
              >
                {busy ? "Importing…" : "Import"}
              </button>
            </>
          }
        >
          <div className="field">
            <label>Keys — one per line, or comma separated</label>
            <textarea
              className="input mono"
              rows={8}
              value={bulk}
              onChange={(e) => setBulk(e.target.value)}
              placeholder="sk-…"
            />
            <div className="hint">
              Duplicates are skipped. Keys are appended, never overwritten.
            </div>
          </div>
          <PasswordField value={pw} onChange={setPw} />
        </Modal>
      )}

      {modal === "remove" && target && (
        <Modal
          title={`Remove ${target.masked}`}
          onClose={() => setModal(null)}
          actions={
            <>
              <button className="btn" onClick={() => setModal(null)}>
                Cancel
              </button>
              <button
                className="btn btn-danger"
                disabled={busy || !pw}
                onClick={() =>
                  act(
                    () => api.removeKey(target.fingerprint, pw),
                    "Key removed",
                  )
                }
              >
                Remove permanently
              </button>
            </>
          }
        >
          <p style={{ fontSize: 12, color: "var(--text-2)", marginBottom: 12 }}>
            This deletes the key from the key file and retires it. It cannot be
            undone without the original value.
          </p>
          <PasswordField value={pw} onChange={setPw} />
        </Modal>
      )}

      {modal === "toggle" && target && (
        <Modal
          title={`${target.enabled ? "Disable" : "Enable"} ${target.masked}`}
          onClose={() => setModal(null)}
          actions={
            <>
              <button className="btn" onClick={() => setModal(null)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={busy || !pw}
                onClick={() =>
                  act(
                    () => api.toggleKey(target.fingerprint, !target.enabled, pw),
                    target.enabled ? "Key disabled" : "Key enabled",
                  )
                }
              >
                {target.enabled ? "Disable" : "Enable"}
              </button>
            </>
          }
        >
          <p style={{ fontSize: 12, color: "var(--text-2)", marginBottom: 12 }}>
            {target.enabled
              ? "The key is commented out of the key file. The running proxy keeps using it until restart."
              : "The key is uncommented and rejoins the pool on next proxy restart."}
          </p>
          <PasswordField value={pw} onChange={setPw} />
        </Modal>
      )}

      {modal === "bulk" && (
        <Modal
          title={`Bulk action on ${sel.size} keys`}
          onClose={() => setModal(null)}
          actions={
            <>
              <button className="btn" onClick={() => setModal(null)}>
                Cancel
              </button>
              <button
                className="btn"
                disabled={busy || !pw}
                onClick={async () => {
                  await act(
                    () => api.bulkToggle([...sel], false, pw),
                    "Keys disabled",
                  );
                }}
              >
                Disable
              </button>
              <button
                className="btn btn-primary"
                disabled={busy || !pw}
                onClick={async () => {
                  await act(
                    () => api.bulkToggle([...sel], true, pw),
                    "Keys enabled",
                  );
                }}
              >
                Enable
              </button>
            </>
          }
        >
          <p style={{ fontSize: 12, color: "var(--text-2)", marginBottom: 12 }}>
            Applies to {sel.size} selected keys. Takes effect on next proxy
            restart.
          </p>
          <PasswordField value={pw} onChange={setPw} />
        </Modal>
      )}
    </>
  );
}
