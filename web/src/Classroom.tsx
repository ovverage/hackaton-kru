import { t, getLocale } from "./i18n.ts";
import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Play,
  Search,
  Video,
  X,
} from "lucide-react";
import {
  Bubble,
  DecisionCard,
  RegMarks,
  Summary,
  Tabs,
} from "./components/Design";
import {
  pendingEvents,
  preparationIssue,
  sessionSummary,
  type TestEnvironment,
} from "./sessionStatus";
import {
  clock,
  eventNames,
  type Device,
  type Exam,
  type Incident,
} from "./types";
import { useDialogFocus } from "./useDialogFocus";

type Props = {
  exam: Exam;
  exams: Exam[];
  devices: Device[];
  events: Incident[];
  lockedOnly: boolean;
  busy: boolean;
  onExam: (id: string) => void;
  onDevice: (id: string) => void;
  onEvent: (id: string) => void;
  onUnlock: (id: string) => void;
  onReview: () => void;
  onStart: () => void;
  onEnd: () => Promise<boolean>;
};

function seatNumber(device: Device, index: number) {
  const match = device.name.match(/(\d+)\D*$/);
  return match ? match[1].padStart(2, "0") : String(index + 1).padStart(2, "0");
}
function bubbleState(device: Device): "on" | "pause" | "off" | "" {
  if (device.state.access === "LOCKED") return "pause";
  if (!device.online) return "off";
  return device.state.lifecycle === "RUNNING" ? "on" : "";
}
function DistractionCounters({ device }: { device: Device }) {
  return (
    <span
      className="cnts"
      aria-label={t(
        "Отвлечения: вниз {0}, влево {1}, вправо {2}",
        device.state.counts.DOWN,
        device.state.counts.LEFT,
        device.state.counts.RIGHT,
      )}
    >
      {(["DOWN", "LEFT", "RIGHT"] as const).map((direction) => (
        <span
          className="cnt"
          key={direction}
          title={t(
            "{0}: {1} из 3",
            direction === "DOWN"
              ? t("Вниз")
              : direction === "LEFT"
                ? t("Влево")
                : t("Вправо"),
            device.state.counts[direction],
          )}
        >
          <span className="counter-arrow">
            {direction === "DOWN" ? "↓" : direction === "LEFT" ? "←" : "→"}
          </span>
          {[1, 2, 3].map((mark) => (
            <Bubble
              key={mark}
              size="xs"
              state={
                device.state.counts[direction] >= mark
                  ? mark === 3
                    ? "pause"
                    : "on"
                  : ""
              }
            />
          ))}
        </span>
      ))}
    </span>
  );
}
function EvidenceThumbnail({ event }: { event?: Incident }) {
  const media = event?.media[0];
  const duration =
    media?.clip_end !== undefined && media?.clip_start !== undefined
      ? media.clip_end - media.clip_start
      : undefined;
  return (
    <span className="cam review-thumb">
      {media ? (
        <>
          {media.mime.startsWith("image/") ? (
            <img src={media.url} alt="" />
          ) : (
            <video
              muted
              playsInline
              preload="metadata"
              src={media.url}
              onLoadedMetadata={(e) => {
                const offset = event!.at - (media.clip_start || 0);
                if (offset > 0 && offset < e.currentTarget.duration)
                  e.currentTarget.currentTime = offset;
              }}
            />
          )}
          <RegMarks />
          {duration !== undefined && duration > 0 && (
            <span className="tc">
              {Math.floor(duration / 60)}:
              {String(Math.floor(duration % 60)).padStart(2, "0")}
            </span>
          )}
        </>
      ) : (
        <>
          <Video size={22} aria-hidden="true" />
          <span className="thumb-empty">
            {event?.media_expired_at ? t("Запись удалена") : t("Без записи")}
          </span>
        </>
      )}
    </span>
  );
}
function EndSessionDialog({
  busy,
  onClose,
  onEnd,
}: {
  busy: boolean;
  onClose: () => void;
  onEnd: () => Promise<boolean>;
}) {
  const ref = useRef<HTMLElement>(null);
  useDialogFocus(ref, () => {
    if (!busy) onClose();
  });
  return (
    <div className="overlay">
      <section
        className="modal"
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={t("Завершить сеанс")}
      >
        <header className="modal-head">
          <div>
            <h2>{t("Завершить сеанс?")}</h2>
            <p>{t("Контроль завершится на всех компьютерах.")}</p>
          </div>
          <button
            className="icon-btn"
            aria-label={t("Закрыть")}
            disabled={busy}
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </header>
        <div className="modal-body">
          <p>{t("События, записи и решения останутся в отчёте.")}</p>
        </div>
        <footer className="modal-foot">
          <button className="btn" disabled={busy} onClick={onClose}>
            {t("Отмена")}
          </button>
          <button
            className="btn primary"
            disabled={busy}
            onClick={async () => {
              if (await onEnd()) onClose();
            }}
          >
            {t("Завершить сеанс")}
          </button>
        </footer>
      </section>
    </div>
  );
}

export default function Classroom(p: Props) {
  const summary = sessionSummary(p.devices, p.events);
  const reviewQueue = pendingEvents(p.events);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState("all");
  const [page, setPage] = useState(0);
  const [ending, setEnding] = useState(false);
  const environment: TestEnvironment =
    p.exam.environment.kind === "BROWSER" ? "BROWSER" : "WINDOW";
  const sorted = [...p.devices].sort((a, b) =>
    a.name.localeCompare(b.name, getLocale(), { numeric: true }),
  );
  const paused = sorted.filter((device) => device.state.access === "LOCKED");
  const roster = sorted.filter((device) =>
    p.lockedOnly
      ? device.state.access === "LOCKED"
      : device.state.access !== "LOCKED",
  );
  const withEvents = roster.filter((device) =>
    p.events.some(
      (event) => event.device_id === device.id && event.decision === "PENDING",
    ),
  );
  const withoutConnection = roster.filter((device) => !device.online);
  const filtered = roster.filter(
    (device) =>
      (tab === "events"
        ? withEvents.includes(device)
        : tab === "offline"
          ? !device.online
          : true) &&
      `${device.student} ${device.name}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 8));
  const currentPage = Math.min(page, pages - 1);
  const ready = p.devices.filter(
    (device) =>
      device.online &&
      device.state.access !== "LOCKED" &&
      device.state.lifecycle === "READY",
  ).length;
  const offline = p.devices.filter(
    (device) => !device.online && device.state.access !== "LOCKED",
  ).length;
  useEffect(() => {
    setPage(0);
    setQuery("");
    setTab("all");
  }, [p.exam.id, p.lockedOnly]);

  return (
    <>
      <section className="session-controls" aria-label={t("Текущий сеанс")}>
        <label className="select session-select">
          <span className="sr-only">{t("Выбрать сеанс")}</span>
          <select
            className="input"
            value={p.exam.id}
            onChange={(event) => p.onExam(event.target.value)}
          >
            {p.exams.map((exam) => (
              <option key={exam.id} value={exam.id}>
                {exam.title}
              </option>
            ))}
          </select>
          <ChevronDown className="i" aria-hidden="true" />
        </label>
        {ready > 0 && (
          <button className="btn primary" disabled={p.busy} onClick={p.onStart}>
            <Play size={15} />
            {t("Начать на")} {ready}{" "}
            {ready === 1 ? t("компьютере") : t("компьютерах")}
          </button>
        )}
        {Object.values(p.exam.participants).some(
          (device) => device.state.lifecycle !== "COMPLETED",
        ) && (
          <button
            className="btn"
            disabled={p.busy}
            onClick={() => setEnding(true)}
          >
            {t("Завершить сеанс")}
          </button>
        )}
      </section>
      <Summary
        running={summary.running}
        paused={paused.length}
        waiting={ready}
        offline={offline}
        pending={summary.pending}
        onReview={p.onReview}
      />
      {paused.length > 0 && (
        <section className="decision-section" aria-labelledby="decide-title">
          <div className="section-title">
            <h2 id="decide-title" className="t-h2">
              {t("Ждут вашего решения")}{" "}
              <span className="danger-text">{paused.length}</span>
            </h2>
            <span className="t-small">
              {t(
                "Продолжить тест можно только отсюда или на компьютере ученика: паролем или по лицу преподавателя.",
              )}
            </span>
          </div>
          <div className="decision-grid">
            {paused.map((device) => {
              const ownEvents = p.events
                .filter((event) => event.device_id === device.id)
                .sort((a, b) => b.created_at - a.created_at);
              const pauseEvent = ownEvents.find(
                (event) => event.type === device.state.reason,
              );
              const reason =
                eventNames[device.state.reason || ""] || t("Тест на паузе");
              const description = device.state.reason?.startsWith("GAZE_")
                ? t("Третья отметка за взгляд в эту сторону в текущем круге")
                : device.state.reason === "PHONE_DETECTED"
                  ? t("Камера увидела телефон два раза подряд")
                  : t("Посмотрите событие и решите, можно ли продолжить тест");
              return (
                <DecisionCard
                  key={device.id}
                  name={device.student}
                  computer={device.name}
                  seat={seatNumber(device, sorted.indexOf(device))}
                  pausedAt={
                    pauseEvent
                      ? clock(pauseEvent.created_at).slice(0, 5)
                      : undefined
                  }
                  reason={reason}
                  description={description}
                  pending={
                    ownEvents.filter((event) => event.decision === "PENDING")
                      .length
                  }
                  thumbnail={
                    pauseEvent ? (
                      <button
                        className="thumbnail-button"
                        aria-label={t(
                          "Запись: {0}, {1}",
                          reason,
                          device.student || device.name,
                        )}
                        onClick={() => p.onEvent(pauseEvent.id)}
                      >
                        <EvidenceThumbnail event={pauseEvent} />
                      </button>
                    ) : (
                      <EvidenceThumbnail />
                    )
                  }
                  onEvent={
                    pauseEvent ? () => p.onEvent(pauseEvent.id) : undefined
                  }
                  onUnlock={() => p.onUnlock(device.id)}
                  busy={p.busy}
                />
              );
            })}
          </div>
        </section>
      )}
      <div className="room-lower">
        <section className="card session-roster">
          <div className="roster-toolbar">
            <h2 className="t-h2">
              {p.lockedOnly ? t("На паузе") : t("Пишут тест")}
            </h2>
            <Tabs
              ariaLabel={t("Фильтр компьютеров")}
              items={[
                { value: "all", label: t("Все"), count: roster.length },
                {
                  value: "events",
                  label: t("С событиями"),
                  count: withEvents.length,
                  tone: "warn",
                },
                {
                  value: "offline",
                  label: t("Нет связи"),
                  count: withoutConnection.length,
                },
              ]}
              value={tab}
              onChange={(value) => {
                setTab(value);
                setPage(0);
              }}
            />
            <label className="roster-search">
              <Search size={15} aria-hidden="true" />
              <input
                aria-label={t("Поиск компьютера или ученика")}
                placeholder={t("Поиск")}
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setPage(0);
                }}
              />
            </label>
          </div>
          <div className="table-box roster-scroll">
            <table className="table dense roster-table">
              <thead>
                <tr>
                  <th>{t("Компьютер")}</th>
                  <th>{t("Отвлечения")}</th>
                  <th>
                    {p.lockedOnly ? t("Причина паузы") : t("Последнее событие")}
                  </th>
                  <th>
                    <span className="sr-only">{t("Открыть")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered
                  .slice(currentPage * 8, currentPage * 8 + 8)
                  .map((device) => {
                    const ownEvents = p.events
                      .filter((event) => event.device_id === device.id)
                      .sort((a, b) => b.created_at - a.created_at);
                    const issue =
                      device.state.lifecycle === "RUNNING" &&
                      device.capabilities.camera_fault
                        ? t("Камера не отвечает")
                        : preparationIssue(device, environment);
                    const latest = ownEvents[0];
                    return (
                      <tr
                        key={device.id}
                        className={
                          device.state.access === "LOCKED" ? "paused" : ""
                        }
                      >
                        <td>
                          <button
                            className="who roster-student"
                            onClick={() => p.onDevice(device.id)}
                          >
                            <Bubble
                              state={bubbleState(device)}
                              flag={ownEvents.some(
                                (event) => event.decision === "PENDING",
                              )}
                            />
                            <span>
                              <strong>
                                {seatNumber(device, sorted.indexOf(device))}{" "}
                                {device.student || device.name}
                              </strong>
                              <small>
                                {device.name},{" "}
                                {device.state.access === "LOCKED"
                                  ? t("тест на паузе")
                                  : !device.online
                                    ? t("нет связи")
                                    : device.state.lifecycle === "RUNNING"
                                      ? t("пишет тест")
                                      : device.state.lifecycle === "COMPLETED"
                                        ? t("тест завершён")
                                        : t("ждёт старта")}
                              </small>
                              {issue && device.online && (
                                <small className="todo">{issue}</small>
                              )}
                            </span>
                          </button>
                        </td>
                        <td>
                          {device.online ? (
                            <DistractionCounters device={device} />
                          ) : (
                            <span className="muted">{t("Нет данных")}</span>
                          )}
                        </td>
                        <td>
                          {device.state.access === "LOCKED" ? (
                            eventNames[device.state.reason || ""] ||
                            t("Тест на паузе")
                          ) : latest ? (
                            eventNames[latest.type] || latest.type
                          ) : (
                            <span className="muted">{t("Событий нет")}</span>
                          )}
                          {latest && (
                            <small
                              className={
                                latest.decision === "PENDING" ? "todo" : ""
                              }
                            >
                              {clock(latest.created_at).slice(0, 5)}
                              {latest.decision === "PENDING"
                                ? t(", ждёт решения")
                                : latest.media.length
                                  ? t(", {0} видео", latest.media.length)
                                  : ""}
                            </small>
                          )}
                        </td>
                        <td>
                          <button
                            className="icon-btn"
                            aria-label={t("Открыть {0}", device.name)}
                            onClick={() => p.onDevice(device.id)}
                          >
                            <ChevronRight size={17} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
          {!filtered.length && (
            <div className="empty">
              {query
                ? t("Компьютер не найден")
                : p.lockedOnly
                  ? t("Компьютеров на паузе нет")
                  : t("Нет компьютеров в этом списке")}
            </div>
          )}
          <footer className="roster-footer">
            <span>
              {t("Показано")}{" "}
              {Math.min(8, Math.max(0, filtered.length - currentPage * 8))}{" "}
              {t("из")} {filtered.length}
            </span>
            <div className="pagination">
              <button
                className="icon-btn"
                aria-label={t("Предыдущая страница")}
                disabled={!currentPage}
                onClick={() => setPage(currentPage - 1)}
              >
                <ChevronLeft size={15} />
              </button>
              <span>
                {currentPage + 1} {t("из")} {pages}
              </span>
              <button
                className="icon-btn"
                aria-label={t("Следующая страница")}
                disabled={currentPage + 1 >= pages}
                onClick={() => setPage(currentPage + 1)}
              >
                <ChevronRight size={15} />
              </button>
            </div>
          </footer>
        </section>
        <aside
          className="card session-review"
          aria-labelledby="session-review-title"
        >
          <header>
            <h2 id="session-review-title" className="t-h2">
              {t("Ждут решения")}{" "}
              <span className="review-count">{summary.pending}</span>
            </h2>
            <button className="btn sm" onClick={p.onReview}>
              {t("Все события")}
            </button>
          </header>
          {reviewQueue.length ? (
            <div className="session-review-list">
              {reviewQueue.map((event) => {
                const device = sorted.find(
                  (item) => item.id === event.device_id,
                );
                const isPause =
                  device?.state.access === "LOCKED" &&
                  device.state.reason === event.type;
                return (
                  <button
                    className="session-review-item"
                    key={event.id}
                    onClick={() => p.onEvent(event.id)}
                  >
                    <EvidenceThumbnail event={event} />
                    <span className="review-item-main">
                      <strong>{eventNames[event.type] || event.type}</strong>
                      <small>
                        {device
                          ? seatNumber(device, sorted.indexOf(device))
                          : event.device_name}{" "}
                        {event.student || event.device_name}
                      </small>
                      <small className={isPause ? "danger-text" : ""}>
                        {clock(event.created_at).slice(0, 5)}
                        {isPause ? t(", тест на паузе") : ""}
                      </small>
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="review-queue-empty">
              <ClipboardCheck size={21} />
              <span>{t("Все события проверены.")}</span>
            </div>
          )}
        </aside>
      </div>
      {ending && (
        <EndSessionDialog
          busy={p.busy}
          onClose={() => setEnding(false)}
          onEnd={p.onEnd}
        />
      )}
    </>
  );
}
