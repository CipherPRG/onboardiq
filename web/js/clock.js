// The replay clock: every wait in a replay goes through sleep(), so Pause, Restart and the speed control work.
import { REDUCED } from "./util.js";

export const clock = { token: 0, paused: false, speed: 2, resume: null };

/** Stop whatever is replaying (a new replay or a live run takes over). */
export function halt() {
  clock.token++;
  clock.paused = false;
  clock.resume = null;
}

export function sleep(ms) {
  const token = clock.token;
  return new Promise((resolve, reject) => {
    const step = () => {
      if (token !== clock.token) return reject(new Error("stopped"));
      if (clock.paused) { clock.resume = step; return; }
      resolve();
    };
    setTimeout(step, REDUCED ? 0 : ms / clock.speed);
  });
}

export function togglePause() {
  clock.paused = !clock.paused;
  if (!clock.paused && clock.resume) {
    const resume = clock.resume;
    clock.resume = null;
    resume();
  }
  return clock.paused;
}

/** Run a replay body; a stop is not an error. */
export async function guarded(body) {
  try { await body(); } catch (e) { if (e.message !== "stopped") throw e; }
}
