// When a device cannot be loaded by the agent itself, it asks a person to start or stop the load.
import { run, useStore } from "../lib/store";
import { confirmPrompt } from "../lib/control";
import { Button } from "./ui";

export function PromptBanner() {
  const p = useStore(run, (s) => s.prompt);
  if (!p) return null;
  return (
    <div role="alertdialog" aria-label="Action needed on the device" className="flex items-center gap-3 px-3 py-2 border-b border-warn/60 bg-warn/10 text-xs">
      <span className="text-warn font-medium">Action needed</span>
      <span>{p.text}</span>
      {p.cmd && <code className="font-mono text-fg bg-bg border border-border rounded-sm px-1.5">{p.cmd}</code>}
      {p.waiting && <Button className="ml-auto" variant="primary" onClick={() => void confirmPrompt()}>
        {/^STOP/i.test(p.text) ? "The load has stopped" : "The load is running"}</Button>}
    </div>
  );
}
