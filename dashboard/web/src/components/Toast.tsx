import { createContext, useContext, useState, ReactNode } from "react";
import { CheckCircle2, AlertTriangle, XCircle } from "lucide-react";

type Toast = { id: number; msg: string; kind: "ok" | "warn" | "bad" };
const Ctx = createContext<(msg: string, kind?: Toast["kind"]) => void>(
  () => {},
);

export function useToast() {
  return useContext(Ctx);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = (msg: string, kind: Toast["kind"] = "ok") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, msg, kind }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 3600);
  };
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="toast-wrap" role="status" aria-live="polite">
        {items.map((t) => (
          <div className={`toast ${t.kind}`} key={t.id}>
            {t.kind === "ok" && <CheckCircle2 size={15} />}
            {t.kind === "warn" && <AlertTriangle size={15} />}
            {t.kind === "bad" && <XCircle size={15} />}
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}