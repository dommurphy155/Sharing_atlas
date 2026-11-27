import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { Card, PageHead, Empty, clock } from "../components/ui";

const TONE: Record<string, string> = {
  ERROR: "err",
  WARNING: "warn",
  SUCCESS: "ok",
};

export default function Logs() {
  const [lines, setLines] = useState<any[]>([]);
  const [level, setLevel] = useState("");
  const [q, setQ] = useState("");
  const [live, setLive] = useState(true);
  const [err, setErr] = useState("");
  const box = useRef<HTMLDivElement>(null);

  async function load() {
    try {
      const r = await api.logs(level, q, 250);
      setLines(r.lines || []);
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  }
  useEffect(() => {
    load();
    if (!live) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [live, level, q]);

  useEffect(() => {
    if (live && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines, live]);

  return (
    <>
      <PageHead
        title="Logs & Diagnostics"
        sub="proxy log tail — secrets are redacted before they reach this page"
        actions={
          <>
            <input
              className="input"
              style={{ width: 200, padding: "3px 8px", fontSize: 12 }}
              placeholder="search text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <select
              className="select"
              style={{ width: 130, padding: "3px 8px", fontSize: 12 }}
              value={level}
              onChange={(e) => setLevel(e.target.value)}
            >
              <option value="">all levels</option>
              {["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"].map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
            <button
              className={`btn ${live ? "btn-primary" : ""}`}
              onClick={() => setLive((v) => !v)}
            >
              {live ? "Live" : "Paused"}
            </button>
          </>
        }
      />

      <Card
        title="Stream"
        sub={`${lines.length} lines`}
        link={
          <span
            className="card-link"
            onClick={() => {
              const txt = lines
                .map((l) => `${clock(l.ts)} ${l.level} ${l.msg}`)
                .join("\n");
              navigator.clipboard?.writeText(txt);
            }}
          >
            copy
          </span>
        }
      >
        {err ? (
          <Empty>{err}</Empty>
        ) : lines.length ? (
          <div className="logview" ref={box}>
            {lines.map((l, i) => (
              <div
                className={`logrow ${TONE[l.level] || ""}`}
                key={`${l.ts}-${i}`}
              >
                <span className="lt">{clock(l.ts)}</span>
                <span className="ll">{l.level}</span>
                <span>{l.msg}</span>
              </div>
            ))}
          </div>
        ) : (
          <Empty>No log lines match</Empty>
        )}
      </Card>
    </>
  );
}
