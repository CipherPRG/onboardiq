// SNMP credentials for the live forms: v1, v2c (community) or v3 (user, authentication, privacy).
// Every passphrase input is masked, and values only go to this machine's own server.
import { el } from "./util.js";

const AUTH = ["SHA", "SHA-256", "SHA-384", "SHA-512", "SHA-224", "MD5"];
const PRIV = ["AES", "AES-192", "AES-256", "DES"];

const field = (label, control) => el("label", null, label, control);
const input = (name, type, attrs) => el("input", { name, type, maxlength: 128, ...attrs });
const select = (name, options) => el("select", { name }, ...options.map((o) => {
  const [value, text] = Array.isArray(o) ? o : [o, o];
  return el("option", { value }, text);
}));

export function mountSnmpAuth(box) {
  const version = select("version", [["2c", "v2c (community)"], ["3", "v3 (user + passphrase)"], ["1", "v1 (community)"]]);
  const community = field("Community", input("community", "password", { value: "public", maxlength: 64 }));
  const v3 = el("div", { class: "v3", hidden: true },
    field("User name", input("v3_user", "text", { maxlength: 64, autocomplete: "off" })),
    field("Authentication", select("v3_auth_proto", AUTH)),
    field("Authentication passphrase", input("v3_auth_pass", "password", { autocomplete: "new-password" })),
    field("Privacy (encryption)", select("v3_priv_proto", PRIV)),
    field("Privacy passphrase (blank = no encryption)", input("v3_priv_pass", "password", { autocomplete: "new-password" })),
    el("p", { class: "hint wide" }, "SNMPv3 needs a user that already exists on the device; passphrases are at least 8 characters. Nothing here is stored or shown again."));
  version.addEventListener("change", () => {
    const is3 = version.value === "3";
    v3.hidden = !is3;
    community.hidden = is3;
  });
  box.append(field("SNMP version", version), community, v3);
}

/** The credential fields of a submitted form, shaped for the server. */
export function snmpFields(fd) {
  const v = fd.get("version") || "2c";
  if (v !== "3") return { version: v, community: fd.get("community") };
  const keys = ["v3_user", "v3_auth_proto", "v3_auth_pass", "v3_priv_proto", "v3_priv_pass"];
  return { version: v, ...Object.fromEntries(keys.map((k) => [k, fd.get(k)])) };
}
