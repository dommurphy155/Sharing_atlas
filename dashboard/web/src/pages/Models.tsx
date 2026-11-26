import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import {
  Card,
  PageHead,
  Badge,
  Empty,
  KV,
  Modal,
  PasswordField,
  fmt,
} from "../components/ui";
import { useToast } from "../components/Toast";

type M = {
  id: string;
  name: string;
  provider: string;
  context: string;
  tasks: string[];
  prompt_price: number;
  completion_price: number;
  is_free: boolean;
  score_hint: number;
};

export default function Models() {
  const [base, setBase] = useState<any>(null);
  const [usage, setUsage] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const [provider, setProvider] = useState("openrouter");
  const [cat, setCat] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState("");
  const [freeOnly, setFreeOnly] = useState(true);
  const [pick, setPick] = useState<M | null>(null);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function loadBase() {
    try {
      const [b, a] = await Promise.all([api.modelProviders(), api.analytics()]);
      setBase(b);
      setUsage(a.models || []);
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  }

  async function loadCat(p = provider, refresh = false, free = freeOnly) {
    setLoading(true);
    try {
      setCat(await api.catalogue(p, refresh, free));
    } catch (e: any) {
      setCat({ ok: false, error: e.message, models: [] });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadBase();
  }, []);
  useEffect(() => {
    loadCat(provider, false, freeOnly);
  }, [provider, freeOnly]);

  const models = useMemo(() => {
    let ms: M[] = cat?.models || [];
    if (q.trim()) {
      const s = q.trim().toLowerCase();
      ms = ms.filter(
        (m) =>
          m.id.toLowerCase().includes(s) ||
          m.name.toLowerCase().includes(s) ||
          m.tasks.some((t) => t.includes(s)),
      );
    }
    return ms;
  }, [cat, q]);

  async function doSwitch() {
    if (!pick) return;
    setBusy(true);
    try {
      await api.switchModel(provider, pick.id, pw);
      toast(`Switched to ${pick.id} — proxy restarted`);
      setPick(null);
      setPw("");
      await loadBase();
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
  if (!base) return <div className="skeleton" style={{ height: 420 }} />;

  const rp = base.active || {};
  const r = base.resolved;
  const activeId = rp.model || r.default_model;
  const activeProvider = rp.provider || r.provider;

  return (
    <>
      <PageHead
        title="Models & Routing"
        sub="switch provider and model the way `atlas switch` does — writes runtime_provider.json, then restarts"
        actions={
          <button
            className="btn"
            disabled={loading}
            onClick={() => loadCat(provider, true, freeOnly)}
          >
            {loading ? "Fetching…" : "Refresh catalogue"}
          </button>
        }
      />

      <div className="strip">
        {[
          ["Provider", activeProvider],
          ["Active model", activeId],
          ["Catalogue", cat ? `${fmt(cat.count ?? 0)} models` : "—"],
          ["Source", cat?.source === "live" ? "live fetch" : cat ? "cache" : "—"],
        ].map(([l, v]) => (
          <div className="strip-item" key={l}>
            <div className="strip-label">{l}</div>
            <div
              className="strip-value"
              style={{ fontSize: 13, fontFamily: "var(--font-mono)" }}
            >
              {v}
            </div>
          </div>
        ))}
      </div>

      <Card
        title="Switch model"
        sub="picking one writes the routing file and restarts the proxy"
        link={
          <div className="row tight" style={{ marginLeft: "auto" }}>
            <input
              className="input"
              style={{ width: 200, padding: "3px 8px", fontSize: 12 }}
              placeholder="search models"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <div className="seg">
              <button
                className={freeOnly ? "on hot" : ""}
                onClick={() => setFreeOnly(true)}
              >
                free
              </button>
              <button
                className={!freeOnly ? "on" : ""}
                onClick={() => setFreeOnly(false)}
              >
                all
              </button>
            </div>
          </div>
        }
      >
        <div className="row" style={{ marginBottom: 12 }}>
          <div className="seg">
            {base.providers.map((p: any) => (
              <button
                key={p.key}
                className={provider === p.key ? "on hot" : ""}
                onClick={() => setProvider(p.key)}
              >
                {p.label}
              </button>
            ))}
          </div>
          <span className="card-sub">
            {base.providers.find((p: any) => p.key === provider)?.description}
          </span>
        </div>

        {cat?.ok === false ? (
          <div className="form-error">{cat.error}</div>
        ) : loading && !cat ? (
          <div className="skeleton" style={{ height: 200 }} />
        ) : models.length ? (
          <div className="table-wrap" style={{ maxHeight: 420 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Tasks</th>
                  <th className="num">Context</th>
                  <th className="num">In / out $</th>
                  <th className="num">Score</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {models.map((m) => {
                  const active =
                    m.id === activeId && provider === activeProvider;
                  return (
                    <tr key={m.id}>
                      <td>
                        <div className="mono" style={{ fontSize: 11.5 }}>
                          {m.id}
                        </div>
                        <div
                          style={{ color: "var(--text-3)", fontSize: 10.5 }}
                        >
                          {m.name}
                        </div>
                      </td>
                      <td>
                        <div className="row tight">
                          {m.tasks.slice(0, 2).map((t) => (
                            <Badge key={t} tone="muted">
                              {t}
                            </Badge>
                          ))}
                        </div>
                      </td>
                      <td className="num">{m.context}</td>
                      <td className="num">
                        {m.is_free ? (
                          <Badge tone="ok">free</Badge>
                        ) : (
                          `${m.prompt_price} / ${m.completion_price}`
                        )}
                      </td>
                      <td className="num">{m.score_hint.toFixed(2)}</td>
                      <td>
                        {active ? (
                          <Badge tone="ok">active</Badge>
                        ) : (
                          <button
                            className="btn btn-sm"
                            onClick={() => {
                              setPick(m);
                              setPw("");
                            }}
                          >
                            Switch
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No models match “{q}”</Empty>
        )}
      </Card>

      <div className="grid grid-2" style={{ marginTop: 12 }}>
        <Card
          title="Active routing"
          sub="data/proxy_data/runtime_provider.json"
        >
          <KV
            rows={[
              ["Provider", <Badge tone="info">{activeProvider}</Badge>],
              ["Model", activeId],
              [
                "Written",
                rp.selected_at
                  ? new Date(rp.selected_at * 1000).toLocaleString()
                  : rp.timestamp
                    ? new Date(rp.timestamp * 1000).toLocaleString()
                    : "—",
              ],
              ["Force default", r.force_default_model ? "yes" : "no"],
              [
                "Context window",
                r.context_window ? `${fmt(r.context_window)} tokens` : "—",
              ],
              [
                "Max input",
                r.max_input_tokens ? `${fmt(r.max_input_tokens)} tokens` : "—",
              ],
            ]}
          />
          <div className="hint" style={{ marginTop: 12 }}>
            Same file and same restart path as{" "}
            <span className="mono">atlas switch</span>. The CLI and this page
            stay in sync because there is only one file.
          </div>
        </Card>

        <Card
          title="Observed usage"
          sub="parsed from the proxy log this session"
        >
          {usage.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th className="num">Requests</th>
                  <th className="num">Errors</th>
                  <th className="num">Rate</th>
                </tr>
              </thead>
              <tbody>
                {usage.map((m) => (
                  <tr key={m.model}>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {m.model}
                    </td>
                    <td className="num">{fmt(m.requests)}</td>
                    <td className="num">{m.errors}</td>
                    <td
                      className="num"
                      style={{
                        color: m.error_rate > 5 ? "var(--bad)" : undefined,
                      }}
                    >
                      {m.error_rate}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No requests observed yet</Empty>
          )}
        </Card>
      </div>

      {pick && (
        <Modal
          title={`Switch to ${pick.id}`}
          onClose={() => setPick(null)}
          actions={
            <>
              <button className="btn" onClick={() => setPick(null)}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                disabled={busy || !pw}
                onClick={doSwitch}
              >
                {busy ? "Switching…" : "Switch & restart"}
              </button>
            </>
          }
        >
          <p
            style={{
              fontSize: 12,
              color: "var(--text-2)",
              marginBottom: 12,
            }}
          >
            Writes <span className="mono">runtime_provider.json</span> with
            provider <strong>{provider}</strong> and model{" "}
            <strong>{pick.id}</strong>, then restarts the proxy. In-flight
            requests are dropped.
          </p>
          <PasswordField value={pw} onChange={setPw} />
        </Modal>
      )}
    </>
  );
}