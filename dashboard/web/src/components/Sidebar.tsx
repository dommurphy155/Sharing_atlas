import {
  LayoutDashboard,
  KeyRound,
  Boxes,
  Activity,
  BarChart3,
  Settings,
  ScrollText,
  Server,
  ShieldCheck,
} from "lucide-react";

export const NAV = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "keypool", label: "Keypool", icon: KeyRound, section: "Manage" },
  { id: "models", label: "Models & Routing", icon: Boxes },
  { id: "config", label: "Configuration", icon: Settings },
  { id: "system", label: "System", icon: Server },
  { id: "live", label: "Live Requests", icon: Activity, section: "Monitor" },
  { id: "analytics", label: "Analytics", icon: BarChart3 },
  { id: "logs", label: "Logs & Diagnostics", icon: ScrollText },
  { id: "security", label: "Security & Account", icon: ShieldCheck, section: "Account" },
];

export default function Sidebar({
  page,
  setPage,
  user,
  open,
}: {
  page: string;
  setPage: (p: string) => void;
  user: string;
  open: boolean;
}) {
  let lastSection = "";
  return (
    <aside className={`sidebar ${open ? "open" : ""}`}>
      <div className="brand">
        <div className="brand-mark">A</div>
        <div>
          <div className="brand-name">Atlas</div>
          <div className="brand-sub">Infrastructure</div>
        </div>
      </div>
      <nav className="nav">
        {NAV.map((n) => {
          const showSection = n.section && n.section !== lastSection;
          if (n.section) lastSection = n.section;
          const Icon = n.icon;
          return (
            <div key={n.id}>
              {showSection && <div className="nav-section">{n.section}</div>}
              <div
                className={`nav-item ${page === n.id ? "active" : ""}`}
                onClick={() => setPage(n.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setPage(n.id)}
              >
                <Icon />
                <span>{n.label}</span>
              </div>
            </div>
          );
        })}
      </nav>
      <div className="sidebar-foot">
        <div className="user-chip">
          <div className="user-avatar">{user.charAt(0).toUpperCase()}</div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 12 }}>{user}</div>
            <div style={{ fontSize: 10.5, color: "var(--text-3)" }}>
              Administrator
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}