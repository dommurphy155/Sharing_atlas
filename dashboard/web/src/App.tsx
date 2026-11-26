import { useEffect, useState } from "react";
import { api } from "./lib/api";
import Sidebar, { NAV } from "./components/Sidebar";
import TopBar from "./components/TopBar";
import Overview from "./pages/Overview";
import Keypool from "./pages/Keypool";
import Models from "./pages/Models";
import Live from "./pages/Live";
import Analytics from "./pages/Analytics";
import Config from "./pages/Config";
import System from "./pages/System";
import Logs from "./pages/Logs";
import Security from "./pages/Security";
import Setup from "./pages/Setup";
import Login from "./pages/Login";
import { ToastProvider } from "./components/Toast";

type Phase = "loading" | "setup" | "login" | "app";

export default function App() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [page, setPage] = useState("overview");
  const [user, setUser] = useState("admin");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  async function boot() {
    try {
      const s = await api.status();
      if (!s.configured) setPhase("setup");
      else if (s.authenticated) {
        setUser((await api.me()).username || "admin");
        setPhase("app");
      } else setPhase("login");
    } catch {
      setPhase("login");
    }
  }
  useEffect(() => {
    boot();
  }, []);

  if (phase === "loading")
    return (
      <div className="auth-wrap">
        <div className="skeleton" style={{ width: 360, height: 240 }} />
      </div>
    );
  if (phase === "setup") return <Setup onDone={boot} />;
  if (phase === "login") return <Login onDone={boot} />;

  const go = (p: string) => {
    if (!NAV.some((n) => n.id === p)) return;
    setPage(p);
    setSidebarOpen(false);
  };

  return (
    <ToastProvider>
      <div className="app-shell">
        {sidebarOpen && (
          <div className="scrim" onClick={() => setSidebarOpen(false)} />
        )}
        <Sidebar page={page} setPage={go} user={user} open={sidebarOpen} />
        <div className="main">
          <TopBar page={page} onMenu={() => setSidebarOpen(true)} />
          <div className="content">
            {page === "overview" && <Overview go={go} />}
            {page === "keypool" && <Keypool />}
            {page === "models" && <Models />}
            {page === "live" && <Live />}
            {page === "analytics" && <Analytics />}
            {page === "config" && <Config />}
            {page === "system" && <System />}
            {page === "logs" && <Logs />}
            {page === "security" && <Security />}
          </div>
        </div>
      </div>
    </ToastProvider>
  );
}