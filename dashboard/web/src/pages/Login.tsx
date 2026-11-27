import { useState } from "react";
import { api } from "../lib/api";

export default function Login({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needs2fa, setNeeds2fa] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setErr("");
    setBusy(true);
    try {
      await api.login(needs2fa ? { password, code } : { password });
      onDone();
    } catch (e: any) {
      // 428 = password accepted, second factor required. Switch the form
      // rather than showing it as a credentials error.
      if (/second factor/i.test(e.message)) {
        setNeeds2fa(true);
        setErr("");
      } else {
        setErr(e.message);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="auth-logo">
          <div className="brand-mark">A</div>
          <div className="auth-title">Atlas</div>
        </div>
        <div className="auth-sub">
          {needs2fa
            ? "Enter the 6-digit code from your authenticator."
            : "Enter your password to access the control centre."}
        </div>
        {err && <div className="form-error">{err}</div>}
        <div className="field">
          <label>Password</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            autoFocus={!needs2fa}
            disabled={needs2fa}
          />
        </div>
        {needs2fa && (
          <div className="field">
            <label>Authentication code</label>
            <input
              className="input"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              autoFocus
            />
          </div>
        )}
        <button
          className="btn btn-primary btn-block"
          disabled={busy || !password || (needs2fa && !code)}
          onClick={submit}
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </div>
    </div>
  );
}
