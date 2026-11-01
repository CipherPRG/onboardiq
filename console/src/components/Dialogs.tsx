// Local server only: onboard any device you can reach (with your own MIB files) and monitor an onboarded one.
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { app, run, ui, useStore } from "../lib/store";
import { Rejoined, startMonitor, startRun } from "../lib/control";
import { getModules, getOnboarded, uploadMibs, type UploadReport } from "../lib/api";
import { Button, Field, toneText } from "./ui";

function Dialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("input,select")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center pt-[8vh]" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} className="w-[620px] max-w-[94vw] max-h-[84vh] overflow-auto bg-panel border border-strong rounded-md">
        <div className="flex items-center h-8 px-3 border-b border-border"><h2 className="text-sm font-medium">{title}</h2>
          <Button variant="ghost" className="ml-auto" onClick={onClose} aria-label="Close">Esc</Button></div>
        <div className="p-3">{children}</div>
      </div>
    </div>
  );
}

const AUTH = ["SHA", "SHA-256", "SHA-384", "SHA-512", "SHA-224", "MD5"];
const PRIV = ["AES", "AES-192", "AES-256", "DES"];

/** SNMP v1/v2c community or v3 user. Values go only to this machine's server; inputs are masked. */
function SnmpAuth() {
  const [v, setV] = useState("2c");
  return (
    <div className="grid grid-cols-3 gap-2 col-span-3">
      <Field label="SNMP version"><select name="version" value={v} onChange={(e) => setV(e.target.value)}>
        <option value="2c">v2c (community)</option><option value="3">v3 (user + passphrase)</option><option value="1">v1 (community)</option></select></Field>
      {v !== "3" ? <Field label="Community"><input name="community" type="password" defaultValue="public" maxLength={64} /></Field> : (
        <>
          <Field label="User"><input name="v3_user" maxLength={64} autoComplete="off" /></Field>
          <Field label="Auth"><select name="v3_auth_proto">{AUTH.map((a) => <option key={a}>{a}</option>)}</select></Field>
          <Field label="Auth passphrase"><input name="v3_auth_pass" type="password" maxLength={128} autoComplete="new-password" /></Field>
          <Field label="Privacy"><select name="v3_priv_proto">{PRIV.map((a) => <option key={a}>{a}</option>)}</select></Field>
          <Field label="Privacy passphrase (blank: none)"><input name="v3_priv_pass" type="password" maxLength={128} autoComplete="new-password" /></Field>
        </>
      )}
    </div>
  );
}

function snmpFields(fd: FormData) {
  const version = String(fd.get("version") || "2c");
  if (version !== "3") return { version, community: fd.get("community") };
  return { version, ...Object.fromEntries(["v3_user", "v3_auth_proto", "v3_auth_pass", "v3_priv_proto", "v3_priv_pass"].map((k) => [k, fd.get(k)])) };
}

const portOf = (fd: FormData) => (fd.get("port") ? Number(fd.get("port")) : null);

/** Drop MIB files: converted on this machine; converted module names are added to the modules field. */
function MibUpload({ onModules }: { onModules: (names: string[]) => void }) {
  const [state, setState] = useState<{ busy: boolean; note: string; report: UploadReport | null }>({ busy: false, note: "", report: null });
  const [over, setOver] = useState(false);
  async function send(list: FileList | null) {
    if (!list?.length) return;
    const files = [...list];
    if (files.length > 25) return setState({ busy: false, note: "At most 25 files at a time.", report: null });
    const big = files.find((f) => f.size > 4_000_000);
    if (big) return setState({ busy: false, note: `${big.name} is larger than 4 MB.`, report: null });
    setState({ busy: true, note: `Converting ${files.length} file${files.length > 1 ? "s" : ""}…`, report: null });
    try {
      const report = await uploadMibs(await Promise.all(files.map(async (f) => ({ name: f.name, text: await f.text() }))));
      onModules(report.converted);
      setState({ busy: false, report, note: report.converted.length ? `${report.converted.length} module(s) ready, added below.` : "Nothing converted. See why below." });
    } catch (e) { setState({ busy: false, note: (e as Error).message, report: null }); }
  }
  const r = state.report;
  return (
    <div className={`col-span-3 border border-dashed rounded-sm p-2 space-y-1 ${over ? "border-accent bg-accent/5" : "border-strong"}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); void send(e.dataTransfer.files); }}>
      <div className="flex items-center gap-2">
        <label className="relative inline-flex items-center h-6 px-2 rounded-sm border border-border bg-raise text-xs cursor-pointer hover:bg-hover focus-within:border-accent">
          Add MIB files<input type="file" multiple className="absolute inset-0 opacity-0 cursor-pointer" disabled={state.busy}
            onChange={(e) => { void send(e.target.files); e.target.value = ""; }} /></label>
        <span className="text-2xs text-muted">or drop them here. Converted on this machine; add the modules they import too.</span>
      </div>
      {state.note && <p className="text-2xs" role="status">{state.note}</p>}
      {r && (
        <ul className="text-2xs font-mono space-y-0.5">
          {r.converted.map((m) => <li key={m}><span className={toneText("ok")}>✓</span> {m}{r.incomplete.includes(m) ? ` (imports ${r.missing_imports[m].join(", ")}, not found)` : ""}</li>)}
          {Object.entries(r.failed).map(([m, why]) => <li key={m}><span className={toneText("crit")}>✗</span> {m}: {r.missing_imports[m] ? `needs ${r.missing_imports[m].join(", ")}. ` : ""}{why}</li>)}
          {r.skipped.map((s) => <li key={s.file} className="text-muted">· {s.file}: {s.reason}</li>)}
        </ul>
      )}
    </div>
  );
}

function useSubmit(start: (fd: FormData) => Promise<void>) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(""); setBusy(true);
    try { await start(new FormData(e.currentTarget)); ui.set({ dialog: null }); }
    catch (err) {
      if (err instanceof Rejoined) { ui.set({ dialog: null }); return; }     // already showing the run in progress
      const message = (err as Error).message;
      setError(message); run.set({ status: "idle", message });             // also on the status line, if the dialog is closed
    }
    finally { setBusy(false); }
  };
  return { error, busy, onSubmit };
}

export function OnboardDialog() {
  const [modules, setModules] = useState("");
  const [suggest, setSuggest] = useState<string[]>([]);
  const [mode, setMode] = useState("read");
  const close = () => ui.set({ dialog: null });
  useEffect(() => {
    const last = modules.split(",").pop()?.trim() ?? "";
    if (last.length < 2) return;
    const id = setTimeout(() => { getModules(last).then(setSuggest).catch(() => setSuggest([])); }, 200);
    return () => clearTimeout(id);
  }, [modules]);
  const prefix = modules.includes(",") ? modules.slice(0, modules.lastIndexOf(",") + 1) : "";
  const { error, busy, onSubmit } = useSubmit(async (fd) => {
    const device = String(fd.get("device"));
    await startRun({ device, modules: modules.split(",").map((m) => m.trim()).filter(Boolean), host: fd.get("host"), port: portOf(fd),
      ...snmpFields(fd), families: fd.getAll("families"), lab: mode === "lab", load_by_hand: mode === "hand" ? fd.get("hand") : null, record: true }, device);
  });
  return (
    <Dialog title="Onboard a device" onClose={close}>
      <form onSubmit={onSubmit} className="grid grid-cols-3 gap-2" autoComplete="off">
        <Field label="What the device is" className="col-span-3"><input name="device" required maxLength={200} placeholder="e.g. MikroTik RouterOS router" /></Field>
        <MibUpload onModules={(names) => setModules((m) => [...new Set([...m.split(",").map((x) => x.trim()).filter(Boolean), ...names])].join(","))} />
        <Field label="MIB modules (comma-separated)" className="col-span-3">
          <input name="modules" required list="oiq-modules" value={modules} onChange={(e) => setModules(e.target.value)} placeholder="HOST-RESOURCES-MIB,UCD-SNMP-MIB" />
          <datalist id="oiq-modules">{suggest.map((m) => <option key={m} value={prefix + m} />)}</datalist>
        </Field>
        <Field label="Host or IP" className="col-span-2"><input name="host" defaultValue="localhost" maxLength={253} /></Field>
        <Field label="Port"><input name="port" inputMode="numeric" placeholder="161" maxLength={5} /></Field>
        <SnmpAuth />
        <fieldset className="col-span-3 flex gap-4 border border-border rounded-sm px-2 py-1.5 text-xs"><legend className="text-2xs text-muted px-1">Metrics</legend>
          {["cpu", "memory", "temperature", "fan"].map((f) => <label key={f} className="flex items-center gap-1"><input type="checkbox" name="families" value={f} defaultChecked /> {f}</label>)}
        </fieldset>
        <fieldset className="col-span-3 grid gap-1 border border-border rounded-sm px-2 py-1.5 text-xs"><legend className="text-2xs text-muted px-1">How to prove CPU and memory picks</legend>
          {[["read", "Read-only checks and a second object"], ["lab", "Load this machine (only for this machine's own agent)"], ["hand", "I will start CPU load on the device when asked"]].map(([v, l]) => (
            <label key={v} className="flex items-center gap-1.5"><input type="radio" name="mode" value={v} checked={mode === v} onChange={() => setMode(v)} /> {l}</label>))}
          {mode === "hand" && <input name="hand" maxLength={300} placeholder="How to start the load, e.g. :while (true) do={}" />}
        </fieldset>
        <div className="col-span-3 flex items-center gap-2 pt-1">
          <Button variant="primary" type="submit" disabled={busy}>Onboard this device</Button>
          {error && <span className="text-xs text-crit" role="alert">{error}</span>}
        </div>
      </form>
    </Dialog>
  );
}

export function MonitorDialog() {
  const onboarded = useStore(app, (s) => s.onboarded);
  const [chosen, setChosen] = useState(onboarded[0]?.name ?? "");
  const pick = onboarded.find((o) => o.name === chosen);
  const close = () => ui.set({ dialog: null });
  useEffect(() => { getOnboarded().then((list) => app.set({ onboarded: list })).catch(() => {}); }, []);
  const { error, busy, onSubmit } = useSubmit(async (fd) => {
    await startMonitor({ onboarded: chosen, host: fd.get("host"), port: portOf(fd), ...snmpFields(fd), interval: Number(fd.get("interval")) || 10,
      explain: fd.get("explain") === "on", alert: fd.get("alert") === "on", record: fd.get("record") === "on" }, pick?.device ?? chosen);
  });
  return (
    <Dialog title="Monitor an onboarded device" onClose={close}>
      <form onSubmit={onSubmit} className="grid grid-cols-3 gap-2" autoComplete="off" key={chosen}>
        <Field label="Onboarded device" className="col-span-3">
          <select value={chosen} onChange={(e) => setChosen(e.target.value)} required>
            {onboarded.length === 0 && <option value="">No onboarded devices yet</option>}
            {onboarded.map((o) => <option key={o.name} value={o.name}>{o.device || o.modules} · {o.name.slice(8)}</option>)}
          </select></Field>
        <Field label="Host or IP" className="col-span-2"><input name="host" defaultValue={pick?.host ?? "localhost"} maxLength={253} /></Field>
        <Field label="Port"><input name="port" inputMode="numeric" defaultValue={pick?.port ?? ""} placeholder="161" maxLength={5} /></Field>
        <SnmpAuth />
        <Field label="Every (seconds)"><input name="interval" inputMode="numeric" defaultValue="10" maxLength={3} /></Field>
        <fieldset className="col-span-3 grid gap-1 border border-border rounded-sm px-2 py-1.5 text-xs"><legend className="text-2xs text-muted px-1">Options</legend>
          <label className="flex items-center gap-1.5"><input type="checkbox" name="explain" defaultChecked /> Explain changes in plain English (Muse Spark)</label>
          <label className="flex items-center gap-1.5"><input type="checkbox" name="alert" defaultChecked /> Email an alert when the level changes (SMTP if set up, otherwise the outbox)</label>
          <label className="flex items-center gap-1.5"><input type="checkbox" name="record" defaultChecked /> Save as a recording</label>
        </fieldset>
        <div className="col-span-3 flex items-center gap-2 pt-1">
          <Button variant="primary" type="submit" disabled={busy || !chosen}>Start monitoring</Button>
          {error && <span className="text-xs text-crit" role="alert">{error}</span>}
        </div>
      </form>
    </Dialog>
  );
}
