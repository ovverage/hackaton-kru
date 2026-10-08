import { t, formatDate, formatTime } from "../i18n.ts";
import {
  useEffect,
  useState,
  type PropsWithChildren,
  type ReactNode,
} from "react";
import {
  Check,
  ChevronRight,
  Pause,
  Play,
  ShieldCheck,
  Video,
  type LucideIcon,
} from "lucide-react";

export function Logo({
  compact = false,
  onDark = false,
}: {
  compact?: boolean;
  onDark?: boolean;
}) {
  return (
    <span className={`brand ${onDark ? "on-dark" : ""}`} aria-label="Qorgau">
      <ShieldCheck className="i" aria-hidden="true" />
      {!compact && (
        <span>
          qorgau<span className="brand-dot">.</span>
        </span>
      )}
    </span>
  );
}

export type BubbleState = "on" | "pause" | "warn" | "ink" | "off" | "";
export function Bubble({
  state = "",
  size = "",
  flag = false,
}: {
  state?: BubbleState;
  size?: "xs" | "sm" | "lg" | "";
  flag?: boolean;
}) {
  return (
    <span
      className={`bub ${state} ${size} ${flag ? "flag" : ""}`}
      aria-hidden="true"
    />
  );
}
export function StepBubble({
  number,
  state = "",
}: {
  number: number;
  state?: "done" | "skip" | "now" | "";
}) {
  return (
    <span
      className={`bub-num ${state}`}
      aria-label={t(
        "Шаг {0}{1}",
        number,
        state === "done" ? t(", выполнен") : "",
      )}
    >
      {state === "done" ? <Check size={16} aria-hidden="true" /> : number}
    </span>
  );
}

/** Render only inside a camera frame (.cam). */
export function RegMarks({ light = true }: { light?: boolean }) {
  return (
    <>
      {["tl", "tr", "bl", "br"].map((corner) => (
        <i
          key={corner}
          className={`reg ${corner} ${light ? "light" : ""}`}
          aria-hidden="true"
        />
      ))}
    </>
  );
}
export function Pill({
  tone = "",
  children,
}: PropsWithChildren<{ tone?: "pause" | "warn" | "ok" | "off" | "" }>) {
  return <span className={`pill ${tone}`}>{children}</span>;
}
export function NavItem({
  icon: Icon,
  children,
  badge,
  tone = "amber",
}: {
  icon: LucideIcon;
  children: ReactNode;
  badge?: number;
  tone?: "red" | "amber";
}) {
  return (
    <>
      <Icon className="i" aria-hidden="true" />
      <span>{children}</span>
      {badge ? (
        <span className={`badge ${tone === "amber" ? "amber" : ""}`}>
          {badge}
        </span>
      ) : null}
    </>
  );
}
export function Clock({ since }: { since?: number }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const elapsed = since
    ? Math.max(0, Math.floor((now.getTime() / 1000 - since) / 60))
    : 0;
  return (
    <span className="clock">
      <strong>
        {formatTime(now, {
          hour: "2-digit",
          minute: "2-digit",
        })}
      </strong>
      <span>
        {formatDate(now, {
          weekday: "long",
          day: "numeric",
          month: "long",
        })}
      </span>
      {since ? (
        <span className="run">
          {t("тест идёт")} {elapsed} {t("мин")}
        </span>
      ) : null}
    </span>
  );
}
export type TabItem = {
  value: string;
  label: string;
  count?: number;
  tone?: "warn";
};
export function Tabs({
  items,
  value,
  onChange,
  ariaLabel = t("Фильтр"),
}: {
  items: TabItem[];
  value: string;
  onChange: (value: string) => void;
  ariaLabel?: string;
}) {
  return (
    <div className="tabs" role="group" aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          type="button"
          className={value === item.value ? "on" : ""}
          key={item.value}
          aria-pressed={value === item.value}
          onClick={() => onChange(item.value)}
        >
          {item.label}
          {item.count !== undefined && (
            <span className={`n ${item.tone || ""}`}>{item.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}
export function Summary({
  running,
  paused,
  waiting,
  offline,
  pending,
  onReview,
}: {
  running: number;
  paused: number;
  waiting: number;
  offline: number;
  pending: number;
  onReview: () => void;
}) {
  const total = running + paused + waiting + offline;
  const cells: {
    count: number;
    label: string;
    state: BubbleState;
    segment: string;
  }[] = [
    { count: running, label: t("пишут тест"), state: "on", segment: "on" },
    { count: paused, label: t("на паузе"), state: "pause", segment: "pause" },
    { count: waiting, label: t("ждут старта"), state: "", segment: "wait" },
    { count: offline, label: t("нет связи"), state: "off", segment: "off" },
  ];
  return (
    <section
      className="card summary"
      aria-label={t("Сводка по {0} компьютерам", total)}
    >
      {cells.map((cell) => (
        <div className="cell" key={cell.label}>
          <b
            className={
              cell.state === "pause"
                ? "danger-text"
                : cell.state === "off"
                  ? "muted"
                  : ""
            }
          >
            {cell.count}
          </b>
          <span>
            <Bubble size="sm" state={cell.state} />
            {cell.label}
          </span>
        </div>
      ))}
      <div
        className="sbar"
        role="img"
        aria-label={cells
          .map((cell) => `${cell.count} ${cell.label}`)
          .join(", ")}
      >
        {cells
          .filter((cell) => cell.count > 0)
          .map((cell) => (
            <i
              key={cell.segment}
              className={cell.segment}
              style={{ flexGrow: cell.count }}
            />
          ))}
      </div>
      <button
        className={`events ${pending ? "" : "empty-events"}`}
        onClick={onReview}
      >
        <b>{pending}</b>
        <span>
          {t("событий ждут решения")}
          <ChevronRight size={15} aria-hidden="true" />
        </span>
      </button>
    </section>
  );
}
export function DecisionCard({
  name,
  computer,
  seat,
  pausedAt,
  reason,
  description,
  pending,
  thumbnail,
  onEvent,
  onUnlock,
  busy,
}: {
  name: string;
  computer: string;
  seat: string;
  pausedAt?: string;
  reason: string;
  description: string;
  pending: number;
  thumbnail: ReactNode;
  onEvent?: () => void;
  onUnlock: () => void;
  busy?: boolean;
}) {
  return (
    <article className="card decide">
      <header className="decide-head">
        <span>
          <Pause size={15} aria-hidden="true" />
          {t("Тест на паузе")}
          {pausedAt ? t(" с {0}", pausedAt) : ""}
        </span>
        <span>
          {t("место")} {seat}
        </span>
      </header>
      <div className="decide-body">
        {thumbnail}
        <div className="decide-details">
          <strong>{name || computer}</strong>
          <span className="t-small">{computer}</span>
          <span className="decide-reason">{reason}</span>
          <span className="t-small">{description}</span>
          {pending > 0 && (
            <span>
              <Pill tone="warn">
                {pending}{" "}
                {pending === 1
                  ? t("событие ждёт")
                  : pending < 5
                    ? t("события ждут")
                    : t("событий ждут")}{" "}
                {t("решения")}
              </Pill>
            </span>
          )}
        </div>
      </div>
      <footer className="decide-foot">
        <button className="btn sm" onClick={onEvent} disabled={!onEvent}>
          <Video size={15} aria-hidden="true" />
          {t("Посмотреть запись")}
        </button>
        <button className="btn sm primary" onClick={onUnlock} disabled={busy}>
          <Play size={15} aria-hidden="true" />
          {t("Продолжить тест")}
        </button>
      </footer>
    </article>
  );
}
export function MiniBar({
  confirmed,
  rejected,
  pending,
}: {
  confirmed: number;
  rejected: number;
  pending: number;
}) {
  return (
    <span
      className="minibar"
      role="img"
      aria-label={t(
        "{0} нарушений, {1} без нарушения, {2} ждут решения",
        confirmed,
        rejected,
        pending,
      )}
    >
      {confirmed > 0 && (
        <i className="confirmed" style={{ flexGrow: confirmed }} />
      )}
      {rejected > 0 && (
        <i className="rejected" style={{ flexGrow: rejected }} />
      )}
      {pending > 0 && <i className="pending" style={{ flexGrow: pending }} />}
    </span>
  );
}
