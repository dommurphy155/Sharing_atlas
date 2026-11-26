import { Menu, LogOut } from "lucide-react";
import { NAV } from "./Sidebar";
import { api } from "../lib/api";

export default function TopBar({
  page,
  onMenu,
}: {
  page: string;
  onMenu: () => void;
}) {
  const label = NAV.find((n) => n.id === page)?.label || page;
  return (
    <header className="topbar">
      <button
        className="icon-btn mobile-menu"
        onClick={onMenu}
        aria-label="Menu"
      >
        <Menu size={15} />
      </button>
      <div className="crumb">
        Atlas <span style={{ opacity: 0.4 }}>/</span> <strong>{label}</strong>
      </div>
      <div className="topbar-right">
        <button
          className="icon-btn"
          aria-label="Sign out"
          title="Sign out"
          onClick={async () => {
            try {
              await api.logout();
            } finally {
              location.reload();
            }
          }}
        >
          <LogOut size={14} />
        </button>
      </div>
    </header>
  );
}