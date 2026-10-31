// Small DOM helpers. Text always goes in as text nodes, never as HTML: session and device text is untrusted.
export const $ = (id) => document.getElementById(id);
export const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const SVGNS = "http://www.w3.org/2000/svg";

function build(node, attrs, kids) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids) {
    if (kid !== null && kid !== undefined && kid !== false) node.append(kid instanceof Node ? kid : String(kid));
  }
  return node;
}

export const el = (tag, attrs, ...kids) => build(document.createElement(tag), attrs, kids);
export const svg = (tag, attrs, ...kids) => build(document.createElementNS(SVGNS, tag), attrs, kids);

export function fmt(v, unit) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  if (unit === "bytes") {
    if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(2) + " GB";
    if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1) + " MB";
    return Math.round(v).toLocaleString() + " B";
  }
  if (unit === "percent") return Math.round(v) + "%";
  if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString();
  return Number(v.toPrecision(3)).toString();
}

export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
