import { useState } from "react";
import { api } from "../lib/api";

export default function Setup({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function complete() {
    setErr("");
    if (password !== confirm) return setErr("Passwords do not match");
    setBusy(true);
    try {
      await api.setupComplete({ password });
      setTimeout(onDone, 700);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="auth-logo">
          <div className="brand-mark">A</div>
          <div className="auth-title">Welcome to Atlas</div>
        </div>
        <div className="auth-sub">
          Set your administrator password. You'll use this to sign in.
        </div>
        {err && <div className="form-error">{err}</div>}
        <div className="field">
          <label>Password</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="12+ chars, mixed"
            autoFocus
          />
        </div>
        <div className="field">
          <label>Confirm password</label>
          <input
            className="input"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && complete()}
          />
        </div>
        <button
          className="btn btn-primary btn-block"
          disabled={busy || !password || !confirm}
          onClick={complete}
        >
          {busy ? "Creating…" : "Create password"}
        </button>
      </div>
    </div>
  );
}
