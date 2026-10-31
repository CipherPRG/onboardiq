// MIB upload on the local page: files are read in the browser, sent to this machine's own server, converted there,
// and the converted module names are added to the form. The report says exactly what to add when something fails.
import { $, el, plural } from "./util.js";
import { post } from "./api.js";

const MAX_FILES = 25;                 // the server's limits (onboardiq/intake.py)
const MAX_BYTES = 4_000_000;

async function read(list) {
  const files = [...list];
  if (files.length > MAX_FILES) throw new Error(`Add at most ${MAX_FILES} files at a time.`);
  const big = files.find((f) => f.size > MAX_BYTES);
  if (big) throw new Error(`${big.name} is larger than 4 MB.`);
  return Promise.all(files.map(async (f) => ({ name: f.name, text: await f.text() })));
}

function line(mark, tone, title, detail) {
  return el("li", { class: tone }, el("span", { class: "mark", "aria-hidden": "true" }, mark),
    el("strong", null, title), detail ? el("span", null, detail) : null);
}

function report(r) {
  const items = [
    ...r.converted.map((m) => line("✓", "pass", m, r.incomplete.includes(m)
      ? `converted, but it imports ${r.missing_imports[m].join(", ")}, which was not found: add it for full names` : "converted")),
    ...Object.entries(r.failed).map(([m, why]) => line("✗", "fail", m,
      (r.missing_imports[m] ? `needs ${r.missing_imports[m].join(", ")}: add those files. ` : "") + why)),
    ...r.skipped.map((s) => line("·", "info", s.file, s.reason)),
  ];
  $("mib-report").replaceChildren(...items);
}

function addModules(input, names) {
  const have = input.value.split(",").map((m) => m.trim()).filter(Boolean);
  input.value = [...new Set([...have, ...names])].join(",");
}

export function wireMibUpload(form) {
  const drop = $("mib-drop"), picker = $("mib-files"), status = $("mib-status");
  async function upload(list) {
    if (!list.length) return;
    $("mib-report").replaceChildren();
    try {
      const files = await read(list);
      status.textContent = `Converting ${plural(files.length, "file")}…`;
      drop.classList.add("busy");
      const r = await post("/api/mibs", { files });
      report(r);
      status.textContent = r.converted.length
        ? `${plural(r.converted.length, "module")} ready. Added to the modules below.`
        : "Nothing converted. The report says what is missing.";
      addModules(form.elements.modules, r.converted);
    } catch (e) {
      status.textContent = e.message;
    } finally {
      drop.classList.remove("busy");
      picker.value = "";
    }
  }
  picker.addEventListener("change", () => upload(picker.files));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files); });
}
