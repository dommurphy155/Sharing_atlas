import { ReactNode, useEffect } from "react";

/* Shared primitives. Everything here is graphite + amber, hairline borders,
   tight radii. No gradients, no glow, no drop-shadow cards. */

export function PageHead({
  title,
  sub,
  actions,
}: {
  title: string;
  sub?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        <div className="page-title">{title}</div>
        {sub && <div className="page-sub">{sub}</div>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  sub,
  link,
  children,
  style,
}: {
  title?: string;
  sub?: string;
  link?: ReactNode;
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div className="card" style={style}>
      {(title || sub) && (
        <div className="card-head">
          {title && <div className="card-title">{title}</div>}
          {sub && <div className="card-sub">{sub}</div>}
          {link}
        </div>
      )}
      {children}
    </div>
  );
}

export function Strip({
  items,
}: {
  items: {
    label: string;
    value: ReactNode;
    unit?: string;
    foot?: string;
    icon?: ReactNode;
    tone?: "bad" | "warn" | "ok";
  }[];
}) {
  return (
    <div className="strip">
      {items.map((it) => (
        <div className="strip-item" key={it.label}>
          <div className="strip-label">
            {it.icon}
            {it.label}
          </div>
          <div
            className="strip-value"
            style={
              it.tone === "bad"
                ? { color: "var(--bad)" }
                : it.tone === "warn"
                  ? { color: "var(--warn)" }
                  : it.tone === "ok"
                    ? { color: "var(--ok)" }
                    : undefined
            }
          >
            {it.value}
            {it.unit && <span className="unit">{it.unit}</span>}
          </div>
          {it.foot && <div className="strip-foot">{it.foot}</div>}
        </div>
      ))}
    </div>
  );
}

export function StatusBar({
  ok,
  name,
  meta,
  right,
}: {
  ok: boolean | null;
  name: string;
  meta?: string;
  right?: ReactNode;
}) {
  const color = ok === null ? "var(--text-3)" : ok ? "var(--ok)" : "var(--bad)";
  return (
    <div className="statusbar">
      <span className="dot" style={{ background: color }} />
      <span className="status-name">{name}</span>
      {meta && <span className="status-meta">{meta}</span>}
      <span className="grow" />
      {right}
    </div>
  );
}

export function Badge({
  tone = "muted",
  children,
}: {
  tone?: "ok" | "warn" | "bad" | "info" | "muted";
  children: ReactNode;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Empty({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="state">
      {icon}
      <div>{children}</div>
    </div>
  );
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Bar({
  segments,
}: {
  segments: { value: number; color: string; label: string }[];
}) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  return (
    <>
      <div className="bar">
        {segments.map((s) => (
          <div
            className="bar-seg"
            key={s.label}
            style={{ width: `${(s.value / total) * 100}%`, background: s.color }}
          />
        ))}
      </div>
      <div className="legend">
        {segments.map((s) => (
          <span key={s.label}>
            <span className="kdot" style={{ background: s.color }} />
            {s.label} {s.value}
          </span>
        ))}
      </div>
    </>
  );
}

/** Minimal sparkline — real samples only, no chart library. */
export function Spark({
  data,
  color = "var(--accent)",
}: {
  data: number[];
  color?: string;
}) {
  if (!data.length) return <div className="spark" />;
  const w = 100;
  const h = 30;
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const span = max - min || 1;
  const pts = data
    .map((v, i) => {
      const x = (i / (data.length - 1 || 1)) * w;
      const y = h - ((v - min) / span) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth="1.25"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export function Modal({
  title,
  onClose,
  children,
  actions,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">{title}</div>
        {children}
        {actions && <div className="modal-actions">{actions}</div>}
      </div>
    </div>
  );
}

/** Password re-auth field, used by every mutating action. */
export function PasswordField({
  value,
  onChange,
  label = "Confirm with your password",
}: {
  value: string;
  onChange: (v: string) => void;
  label?: string;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <input
        className="input"
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="current-password"
      />
    </div>
  );
}

export function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString();
}

/** Milliseconds → seconds, always. 1234 → "1.2s", 340 → "0.3s". */
export function secs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 100) return `${(ms / 1000).toFixed(2)}s`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function ago(ts: number): string {
  const s = Math.floor(Date.now() / 1000) - ts;
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
