import type { PropsWithChildren, ReactNode } from "react";

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="brand" aria-label="Qorgau">
      <svg className="logo" viewBox="0 0 32 32" aria-hidden="true">
        <rect x="2" y="2" width="28" height="28" rx="8" fill="currentColor" />
        <circle cx="16" cy="15" r="7" fill="white" />
        <circle cx="16" cy="15" r="3" fill="currentColor" />
        <path d="M20 20.5 25 26" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
      {!compact && <span>Qorgau</span>}
    </span>
  );
}

export type BubbleState = "on" | "pause" | "warn" | "ink" | "off" | "";

export function Bubble({ state = "", size = "", flag = false }: { state?: BubbleState; size?: "xs" | "sm" | "lg" | ""; flag?: boolean }) {
  return <span className={`bub ${state} ${size} ${flag ? "flag" : ""}`} aria-hidden="true" />;
}

export function StepBubble({ number, state = "" }: { number: number; state?: "done" | "skip" | "now" | "" }) {
  return <span className={`bub-num ${state}`}>{number}</span>;
}

export function Sheet({ children, className = "" }: PropsWithChildren<{ className?: string }>) {
  return (
    <section className={`sheet ${className}`}>
      {children}
    </section>
  );
}

export function RegMarks({ light = false }: { light?: boolean }) {
  return <>{["tl", "tr", "bl", "br"].map((corner) => <i key={corner} className={`reg ${corner} ${light ? "light" : ""}`} aria-hidden="true" />)}</>;
}

export function Pill({ tone = "", children }: PropsWithChildren<{ tone?: "pause" | "warn" | "ok" | "off" | "" }>) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function NavOption({ children, badge }: { children: ReactNode; badge?: number }) {
  return <><span className="opt" aria-hidden="true" /><span>{children}</span>{badge ? <span className="badge amber">{badge}</span> : null}</>;
}

export function Clock({ since }: { since?: number }) {
  const now = new Date();
  const elapsed = since ? Math.max(0, Math.floor(Date.now() / 1000 - since)) : 0;
  const duration = `${String(Math.floor(elapsed / 3600)).padStart(2, "0")}:${String(Math.floor(elapsed % 3600 / 60)).padStart(2, "0")}`;
  return (
    <span className="clock">
      <strong>{now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</strong>
      <span>{now.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}</span>
      {since ? <span className="run">Тест идёт {duration}</span> : null}
    </span>
  );
}
