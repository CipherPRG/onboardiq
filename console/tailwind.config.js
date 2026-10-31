/** Dense operations-console tokens: 11–14px type, 2–4px radius, 1px borders, no shadows, exact status colours. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    fontFamily: {
      sans: ["Geist", "Inter", "system-ui", "sans-serif"],
      mono: ["Geist Mono", "ui-monospace", "SFMono-Regular", "monospace"],
    },
    fontSize: { "2xs": ["11px", "16px"], xs: ["12px", "16px"], sm: ["13px", "18px"], base: ["14px", "20px"] },
    borderRadius: { none: "0", sm: "2px", DEFAULT: "3px", md: "4px", full: "9999px" },
    extend: {
      colors: {
        bg: "#0b0d10", panel: "#111418", raise: "#171b21", hover: "#1d222a",
        border: "#262c35", strong: "#38414d",
        fg: "#e3e7ec", muted: "#8b95a3", faint: "#5d6672",
        accent: "#4c8dff",
        crit: "#DC2626", warn: "#D97706", ok: "#059669", info: "#4c8dff",
      },
    },
  },
  plugins: [],
};
