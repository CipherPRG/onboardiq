// A message over the page for a refused click or a failed action. It goes away by itself and can be closed.
import { useEffect, useState } from "react";
import { toast, useStore } from "../lib/store";

export function Toast() {
  const { text, title, id } = useStore(toast, (s) => ({ text: s.text, title: s.title, id: s.id }));
  const [shown, setShown] = useState(0);                   // the id the person has already closed or let expire
  useEffect(() => {
    if (!id) return;
    setShown(0);
    const timer = setTimeout(() => setShown(id), 9000);
    return () => clearTimeout(timer);
  }, [id]);
  if (!id || shown === id) return null;
  return (
    <div role="alert" className="fixed top-12 left-1/2 -translate-x-1/2 z-50 max-w-[min(560px,92vw)] flex items-start gap-3 px-4 py-3 rounded border border-crit/60 bg-panel text-sm text-fg">
      <span className="text-crit font-semibold shrink-0">{title}</span>
      <span className="flex-1">{text}</span>
      <button onClick={() => setShown(id)} aria-label="Close message" className="text-muted hover:text-fg leading-none text-base">×</button>
    </div>
  );
}
