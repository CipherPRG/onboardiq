// The few page regions every view writes to.
import { $ } from "./util.js";

export const ui = {
  status: (text) => { $("status").textContent = text; },
  device: (text) => { $("device").textContent = text; },
  host: () => $("run-host"),
  clear() { $("run-host").replaceChildren(); },
};
