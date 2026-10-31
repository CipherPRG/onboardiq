// The hero device: port lights, and a slight turn toward the pointer. Decoration only (aria-hidden in the page).
import { $, el, REDUCED } from "./util.js";

export function mountRig() {
  const ports = $("ports");
  for (let i = 0; i < 24; i++) {
    const port = el("i", { class: i % 7 === 3 ? "amber" : null });
    port.style.setProperty("--d", `${0.6 + ((i * 37) % 17) / 10}s`);
    port.style.setProperty("--o", `${-((i * 53) % 23) / 10}s`);
    ports.append(port);
  }
  if (REDUCED) return;
  const rig = $("rig");
  const hero = rig.closest(".hero");
  hero.addEventListener("pointermove", (e) => {
    const r = hero.getBoundingClientRect();
    rig.style.setProperty("--ry", `${((e.clientX - r.left) / r.width - 0.5) * 10}deg`);
    rig.style.setProperty("--rx", `${-((e.clientY - r.top) / r.height - 0.5) * 8}deg`);
  });
  hero.addEventListener("pointerleave", () => { rig.style.removeProperty("--rx"); rig.style.removeProperty("--ry"); });
}
