// Typed API client. credentials: include for cookie-based sessions.
// CSRF double-submit: reads atlas_csrf cookie, sends x-csrf-token on mutations.
//
// BASE resolves the mount point: same-origin root normally, or a reverse-proxy
// subpath (/atlas/) when served under one. Relative base in vite.config keeps
// asset URLs working in both placements.

const BASE = (() => {
  const m = document.querySelector("meta[name='atlas-base']");
  if (m?.getAttribute("content"))
    return m.getAttribute("content")!.replace(/\/$/, "");
  const p = window.location.pathname;
  const i = p.indexOf("/atlas");
  return i >= 0 ? "/atlas" : "";
})();

const REQUEST_TIMEOUT = 30_000; // 30s

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)atlas_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const method = (opts.method || "GET").toUpperCase();
  const headers: Record<string, string> = { ...(opts.headers as any) };
  if (method !== "GET") headers["x-csrf-token"] = csrfToken();
  if (opts.body) headers["content-type"] = "application/json";

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

  try {
    const res = await fetch(`${BASE}/api${path}`, {
      ...opts,
      headers,
      credentials: "include",
      signal: controller.signal,
    });

    // Validate content-type is JSON (defense against unexpected HTML error pages)
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("application/json") && res.ok) {
      throw new Error("Unexpected response format");
    }

    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = await res.json();
        // Support both old format (detail) and new format (error.detail)
        detail = j.detail || j.error?.detail || detail;
      } catch {
        /* ignore parse errors */
      }
      throw new Error(detail);
    }
    return res.json() as Promise<T>;
  } finally {
    clearTimeout(timeout);
  }
}

export const api = {
  status: () => req<any>("/auth/status"),
  me: () => req<any>("/auth/me"),
  setupComplete: (b: any) =>
    req<any>("/setup/complete", { method: "POST", body: JSON.stringify(b) }),
  login: (b: any) =>
    req<any>("/auth/login", { method: "POST", body: JSON.stringify(b) }),
  logout: () => req<any>("/auth/logout", { method: "POST" }),

  overview: () => req<any>("/overview"),
  systemStatus: () => req<any>("/system/status"),
  diagnostics: () => req<any>("/system/diagnostics"),
  updates: () => req<any>("/system/updates"),
  backups: () => req<any>("/system/backups"),
  unitAction: (unit: string, action: string, password: string) =>
    req<any>(`/system/${unit}/${action}`, {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  backup: (password: string, label = "") =>
    req<any>("/system/backup", {
      method: "POST",
      body: JSON.stringify({ password, label }),
    }),

  keys: (limit = 400, offset = 0) =>
    req<any>(`/keys?limit=${limit}&offset=${offset}`),
  keysPage: (offset: number, limit: number) =>
    req<any>(`/keys?limit=${limit}&offset=${offset}`),
  importKeys: (keys: string[], password: string) =>
    req<any>("/keys/import", {
      method: "POST",
      body: JSON.stringify({ keys, password }),
    }),
  removeKey: (fingerprint: string, password: string) =>
    req<any>("/keys/remove", {
      method: "POST",
      body: JSON.stringify({ fingerprint, password }),
    }),
  toggleKey: (fingerprint: string, enabled: boolean, password: string) =>
    req<any>("/keys/toggle", {
      method: "POST",
      body: JSON.stringify({ fingerprint, enabled, password }),
    }),
  bulkToggle: (fingerprints: string[], enabled: boolean, password: string) =>
    req<any>("/keys/bulk-toggle", {
      method: "POST",
      body: JSON.stringify({ fingerprints, enabled, password }),
    }),

  config: () => req<any>("/config"),
  env: () => req<any>("/config/env"),

  modelProviders: () => req<any>("/models/providers"),
  catalogue: (provider: string, refresh = false, freeOnly = true) =>
    req<any>(
      `/models/catalogue?provider=${encodeURIComponent(provider)}&refresh=${refresh}&free_only=${freeOnly}`,
    ),
  switchModel: (provider: string, model: string, password: string) =>
    req<any>("/models/switch", {
      method: "POST",
      body: JSON.stringify({ provider, model, password }),
    }),
  setProvider: (provider: string, model: string, password: string) =>
    req<any>("/config/provider", {
      method: "POST",
      body: JSON.stringify({ provider, model, password }),
    }),
  previewEnv: (values: Record<string, string>, password: string) =>
    req<any>("/config/env/preview", {
      method: "POST",
      body: JSON.stringify({ values, password }),
    }),
  writeEnv: (values: Record<string, string>, password: string) =>
    req<any>("/config/env", {
      method: "POST",
      body: JSON.stringify({ values, password }),
    }),

  live: () => req<any>("/live"),
  liveErrors: () => req<any>("/live/errors"),
  logs: (level = "", q = "", limit = 200) =>
    req<any>(
      `/logs?level=${encodeURIComponent(level)}&q=${encodeURIComponent(q)}&limit=${limit}`,
    ),

  analytics: () => req<any>("/analytics"),
  series: (hours = 6) => req<any>(`/analytics/series?hours=${hours}`),

  security: () => req<any>("/security"),
  changePassword: (current: string, next: string) =>
    req<any>("/security/password", {
      method: "POST",
      body: JSON.stringify({ current, new: next }),
    }),
  revokeSessions: (session_id = "") =>
    req<any>("/security/sessions/revoke", {
      method: "POST",
      body: JSON.stringify({ session_id }),
    }),

  // Audit chain verification
  auditVerify: () => req<any>("/security/audit/verify"),
};

export { csrfToken };
