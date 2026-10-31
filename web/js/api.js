// Talking to the local OnboardIQ server (absent on static hosting, where only replays work).
export async function post(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-OnboardIQ": "1" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `request failed (${r.status})`);
  return data;
}

/** Follow a run's events. `onClose` fires once, when the run ends or the connection drops. */
export function stream(id, onEvent, onClose) {
  const es = new EventSource(`/api/runs/${id}/events`);
  let closed = false;
  const close = () => { if (!closed) { closed = true; es.close(); onClose?.(); } };
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    onEvent(ev);
    if (ev.kind === "end" || ev.kind === "error") close();
  };
  es.onerror = close;
}
