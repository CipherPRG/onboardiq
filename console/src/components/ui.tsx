// Small primitives in the shadcn/ui manner, tuned for density: 2–4px radius, 1px borders, no shadows.
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { Tone } from "../lib/format";

export const cn = (...xs: (string | false | null | undefined)[]) => xs.filter(Boolean).join(" ");

const TONE_TEXT: Record<Tone, string> = { ok: "text-ok", warn: "text-warn", crit: "text-crit", muted: "text-muted", info: "text-info" };
const TONE_PILL: Record<Tone, string> = {
  ok: "text-ok border-ok/50 bg-ok/10", warn: "text-warn border-warn/50 bg-warn/10", crit: "text-crit border-crit/50 bg-crit/10",
  muted: "text-muted border-border bg-raise", info: "text-info border-info/50 bg-info/10",
};
export const toneText = (t: Tone) => TONE_TEXT[t];

export function Pill({ tone, children, className }: { tone: Tone; children: ReactNode; className?: string }) {
  return <span className={cn("inline-flex items-center h-[18px] px-1.5 rounded-sm border text-2xs font-mono whitespace-nowrap", TONE_PILL[tone], className)}>{children}</span>;
}

export function Dot({ tone }: { tone: Tone }) {
  const bg = { ok: "bg-ok", warn: "bg-warn", crit: "bg-crit", muted: "bg-faint", info: "bg-info" }[tone];
  return <span className={cn("inline-block w-1.5 h-1.5 rounded-full", bg)} aria-hidden="true" />;
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "ghost" | "danger" };
export function Button({ variant = "default", className, ...rest }: BtnProps) {
  const v = {
    default: "border-border bg-raise hover:bg-hover text-fg",
    primary: "border-accent bg-accent text-white hover:bg-[#5e99ff]",
    ghost: "border-transparent hover:bg-hover text-muted hover:text-fg",
    danger: "border-crit/60 text-crit hover:bg-crit/10",
  }[variant];
  return <button className={cn("inline-flex items-center gap-1.5 h-6 px-2 rounded-sm border text-xs font-medium whitespace-nowrap", v, className)} {...rest} />;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="px-1 h-4 inline-flex items-center rounded-sm border border-border text-2xs font-mono text-faint">{children}</kbd>;
}

export function PanelHeader({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex items-center gap-2 h-7 px-2 border-b border-border bg-panel shrink-0">
      <span className="text-xs font-medium text-fg">{title}</span>
      <div className="ml-auto flex items-center gap-1">{children}</div>
    </div>
  );
}

export function Field({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return <label className={cn("flex flex-col gap-1 text-2xs text-muted", className)}>{label}{children}</label>;
}
