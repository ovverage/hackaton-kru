import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  Info,
  Monitor,
  MoreHorizontal,
  Play,
  Search,
  SlidersHorizontal,
  Video,
  X,
} from "lucide-react";
import {
  api,
  clock,
  eventNames,
  type Device,
  type Exam,
  type Incident,
} from "./types";

type Props = {
  exam: Exam;
  exams: Exam[];
  devices: Device[];
  events: Incident[];
  busy: boolean;
  onExam: (id: string) => void;
  onDevice: (id: string) => void;
  onEvent: (id: string) => void;
  onStart: () => void;
  onEnd: () => Promise<void>;
  onUpdate: () => Promise<void>;
  notify: (text: string) => void;
};

export default function Classroom(p: Props) {
  const [query, setQuery] = useState(""),
    [attention, setAttention] = useState(false);
  const [selected, setSelected] = useState<string | null>(null),
    [page, setPage] = useState(0);
  const [ending, setEnding] = useState(false);
  const pending = p.events.filter((e) => e.decision === "PENDING");
  const needsAttention = (d: Device) =>
    d.state.access === "LOCKED" ||
    (!d.online && d.state.lifecycle !== "COMPLETED") ||
    pending.some((e) => e.device_id === d.id);
  const filtered = p.devices.filter(
    (d) =>
      (!attention || needsAttention(d)) &&
      `${d.student} ${d.name}`.toLowerCase().includes(query.toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 8));
  const currentPage = Math.min(page, pages - 1);
  const incident = pending.find((e) => e.id === selected) || pending[0];
  const target = p.devices
    .flatMap((d) => d.targets || [])
    .find((t) => t.id === p.exam.environment.target_id);
  const environment =
    p.exam.environment.kind === "BROWSER"
      ? `${target?.name || "Браузер"} · ${new URL(p.exam.environment.url!).hostname}`
      : target?.name || "Приложение";
  useEffect(() => {
    setSelected(null);
    setPage(0);
    setQuery("");
    setAttention(false);
  }, [p.exam.id]);
  useEffect(() => {
    if (!selected || !pending.some((e) => e.id === selected))
      setSelected(pending[0]?.id || null);
  }, [p.events, selected]);
  return (
    <>
      <section className="class-session" aria-label="Текущий сеанс">
        <span className="session-status">
          <i
            className={`status-dot ${p.exam.status === "RUNNING" ? "green" : "blue"}`}
          />
          {p.exam.status === "RUNNING"
            ? "Контроль идёт"
            : p.exam.status === "COMPLETED"
              ? "Сеанс завершён"
              : "Готов к запуску"}
        </span>
        <span className="session-room">{p.exam.room}</span>
        <span className="session-environment" title={p.exam.environment.url}>
          <Monitor size={18} />
          {environment}
        </span>
        <select
          aria-label="Выбрать сеанс"
          value={p.exam.id}
          onChange={(e) => p.onExam(e.target.value)}
        >
          {p.exams.map((e) => (
            <option key={e.id} value={e.id}>
              {e.title}
            </option>
          ))}
        </select>
        {p.devices.some((d) => d.state.lifecycle === "READY") && (
          <button className="btn primary" disabled={p.busy} onClick={p.onStart}>
            <Play size={15} />
            Начать контроль
          </button>
        )}
        {p.devices.some((d) => d.state.lifecycle === "RUNNING") && (
          <button
            className="btn"
            disabled={p.busy}
            onClick={() => setEnding(true)}
          >
            Завершить сеанс
          </button>
        )}
      </section>
      <div className="class-metrics">
        <div>
          <strong>{p.devices.length}</strong>
          <span>Учеников</span>
        </div>
        <div>
          <strong>
            {
              p.devices.filter(
                (d) =>
                  d.online &&
                  d.state.lifecycle === "RUNNING" &&
                  d.state.access === "OPEN",
              ).length
            }
          </strong>
          <span>Под контролем</span>
        </div>
        <div>
          <strong>
            {p.devices.filter((d) => d.state.access === "LOCKED").length}
          </strong>
          <span>Приостановлено</span>
        </div>
        <div>
          <strong>{pending.length}</strong>
          <span>На проверке</span>
        </div>
      </div>
      <div className="class-layout">
        <section className="class-roster">
          <div className="roster-toolbar">
            <div className="roster-tabs" aria-label="Фильтр учеников">
              <button
                className={!attention ? "selected" : ""}
                onClick={() => {
                  setAttention(false);
                  setPage(0);
                }}
              >
                Все ученики <b>{p.devices.length}</b>
              </button>
              <button
                className={attention ? "selected" : ""}
                onClick={() => {
                  setAttention(true);
                  setPage(0);
                }}
              >
                Требуют внимания{" "}
                <b>{p.devices.filter(needsAttention).length}</b>
              </button>
            </div>
            <div className="roster-search">
              <Search size={16} />
              <input
                aria-label="Поиск ученика"
                placeholder="Найти ученика"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(0);
                }}
              />
            </div>
          </div>
          <div className="roster-scroll">
            <table className="roster-table">
              <thead>
                <tr>
                  <th>Ученик</th>
                  <th>Статус</th>
                  <th>Вниз</th>
                  <th>Влево</th>
                  <th>Вправо</th>
                  <th>Последнее событие</th>
                  <th>
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered
                  .slice(currentPage * 8, currentPage * 8 + 8)
                  .map((d, i) => {
                    const latest = p.events.find((e) => e.device_id === d.id);
                    const review = pending.find((e) => e.device_id === d.id);
                    const locked = d.state.access === "LOCKED";
                    const status = locked
                      ? "Приостановлен"
                      : d.state.lifecycle === "COMPLETED"
                        ? "Завершил"
                        : !d.online
                          ? "Нет связи"
                          : review
                            ? "На проверке"
                            : d.state.lifecycle === "RUNNING"
                              ? "Под контролем"
                              : "Ожидает старта";
                    const tone = locked
                      ? "red"
                      : !d.online || review
                        ? "amber"
                        : d.state.lifecycle === "RUNNING"
                          ? "green"
                          : "muted";
                    return (
                      <tr
                        key={d.id}
                        className={
                          incident?.device_id === d.id ? "is-highlighted" : ""
                        }
                      >
                        <td>
                          <button
                            className="roster-student"
                            onClick={() => p.onDevice(d.id)}
                          >
                            <span className={`student-initials shade-${i % 4}`}>
                              {d.student
                                .split(/\s+/)
                                .slice(0, 2)
                                .map((n) => n[0])
                                .join("")}
                            </span>
                            <span>
                              <strong>{d.student}</strong>
                              <small>
                                {d.name}
                                {d.simulated ? " · стенд" : ""}
                              </small>
                            </span>
                          </button>
                        </td>
                        <td>
                          <span className="roster-status">
                            <i className={`status-dot ${tone}`} />
                            {status}
                          </span>
                        </td>
                        {(["DOWN", "LEFT", "RIGHT"] as const).map(
                          (direction) => (
                            <td
                              key={direction}
                              className={`roster-count ${d.state.counts[direction] >= 3 ? "limit" : ""}`}
                            >
                              {d.state.counts[direction]}
                              <span>/3</span>
                            </td>
                          ),
                        )}
                        <td>
                          {latest ? (
                            <button
                              className="roster-event"
                              onClick={() =>
                                latest.decision === "PENDING"
                                  ? setSelected(latest.id)
                                  : p.onEvent(latest.id)
                              }
                            >
                              {eventNames[latest.type] || latest.type}
                              <small>
                                {clock(latest.created_at).slice(0, 5)}
                              </small>
                            </button>
                          ) : (
                            <span className="no-events">Нет событий</span>
                          )}
                        </td>
                        <td>
                          <button
                            className="icon-btn"
                            aria-label={`Открыть ${d.student}`}
                            onClick={() => {
                              if (review) setSelected(review.id);
                              p.onDevice(d.id);
                            }}
                          >
                            <MoreHorizontal size={19} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
          {!filtered.length && (
            <div className="empty">По этому запросу учеников нет</div>
          )}
          <footer className="roster-footer">
            <span>
              Показаны{" "}
              {Math.min(8, Math.max(0, filtered.length - currentPage * 8))} из{" "}
              {filtered.length}
            </span>
            <div className="pagination">
              <button
                aria-label="Предыдущая страница"
                disabled={!currentPage}
                onClick={() => setPage(currentPage - 1)}
              >
                <ChevronLeft size={15} />
              </button>
              <span>
                {currentPage + 1} / {pages}
              </span>
              <button
                aria-label="Следующая страница"
                disabled={currentPage + 1 >= pages}
                onClick={() => setPage(currentPage + 1)}
              >
                <ChevronRight size={15} />
              </button>
            </div>
          </footer>
        </section>
        <aside className="review-rail" aria-label="События на проверке">
          <header>
            <h2>
              На проверке <span>{pending.length}</span>
            </h2>
            <SlidersHorizontal size={17} aria-label="Сначала новые" />
          </header>
          {incident ? (
            <InlineReview
              key={incident.id}
              event={incident}
              onUpdate={p.onUpdate}
              notify={p.notify}
              onDetails={() => p.onEvent(incident.id)}
            />
          ) : (
            <div className="review-clear">
              <CheckCheck size={32} />
              <h3>Всё проверено</h3>
              <p>Новые события появятся здесь во время теста.</p>
            </div>
          )}
          <div className="review-queue">
            {pending
              .filter((e) => e.id !== incident?.id)
              .slice(0, 4)
              .map((e) => (
                <button key={e.id} onClick={() => setSelected(e.id)}>
                  <i
                    className={`status-dot ${e.category === "CRITICAL" ? "red" : "amber"}`}
                  />
                  <span>
                    <strong>{eventNames[e.type] || e.type}</strong>
                    <small>
                      {e.student} · {e.device_name}
                    </small>
                  </span>
                  <time>{clock(e.created_at).slice(0, 5)}</time>
                </button>
              ))}
          </div>
          <footer>
            <Info size={16} />
            <span>Решение принимает преподаватель</span>
          </footer>
        </aside>
      </div>
      <div className="class-mode">
        <Info size={15} />
        <span>
          {p.exam.simulated
            ? "Тренировочный стенд · события создаются вручную, камера и блокировка ОС не используются."
            : "Режим задаётся при создании сеанса: наблюдение или ограничение Windows. События проверяет преподаватель."}
        </span>
      </div>
      {ending && (
        <div className="overlay">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label="Завершить сеанс"
          >
            <header>
              <h2>Завершить сеанс?</h2>
              <button
                className="icon-btn"
                aria-label="Закрыть"
                disabled={p.busy}
                onClick={() => setEnding(false)}
              >
                <X size={19} />
              </button>
            </header>
            <div className="modal-body">
              <p>
                Команда завершения будет отправлена всем участникам этого
                сеанса. События и решения останутся в отчёте.
              </p>
              <div className="button-row end">
                <button
                  className="btn"
                  disabled={p.busy}
                  onClick={() => setEnding(false)}
                >
                  Отмена
                </button>
                <button
                  className="btn primary"
                  disabled={p.busy}
                  onClick={async () => {
                    await p.onEnd();
                    setEnding(false);
                  }}
                >
                  Завершить сеанс
                </button>
              </div>
            </div>
          </section>
        </div>
      )}
    </>
  );
}

function InlineReview({
  event: e,
  onUpdate,
  notify,
  onDetails,
}: {
  event: Incident;
  onUpdate: () => Promise<void>;
  notify: (s: string) => void;
  onDetails: () => void;
}) {
  const [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false);
  const media = e.media[0];
  async function decide(decision: string) {
    setBusy(true);
    try {
      await api(`/events/${e.id}/review`, {
        decision,
        reason: reason.trim(),
        expected_revision: e.revision,
      });
      await onUpdate();
      notify("Решение сохранено");
    } catch (err) {
      notify((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="review-feature">
      <div className="review-title">
        <i
          className={`status-dot ${e.category === "CRITICAL" ? "red" : "amber"}`}
        />
        <strong>{eventNames[e.type] || e.type}</strong>
        <time>{clock(e.created_at)}</time>
      </div>
      <p className="review-person">
        {e.student} · {e.device_name}
      </p>
      {media ? (
        <video
          key={media.id}
          controls
          preload="metadata"
          src={media.url}
          onLoadedMetadata={(v) => {
            const offset = e.at - (media.clip_start || 0);
            if (offset > 0 && offset < v.currentTarget.duration)
              v.currentTarget.currentTime = offset;
          }}
        />
      ) : (
        <div className="review-no-video">
          <Video size={30} />
          <strong>Фрагмент не прикреплён</strong>
          <span>
            {e.simulated
              ? "Тренировочное событие"
              : "Ожидаем передачу от агента"}
          </span>
          <button onClick={onDetails}>
            Открыть событие <ArrowUpRight size={13} />
          </button>
        </div>
      )}
      <div className="review-caption">
        <span>Срабатывание: {e.at.toFixed(1)} с</span>
        <button onClick={onDetails}>
          Подробнее <ArrowUpRight size={13} />
        </button>
      </div>
      <label className="review-reason">
        Комментарий к решению
        <textarea
          value={reason}
          maxLength={500}
          placeholder="Что видно на записи?"
          onChange={(v) => setReason(v.target.value)}
        />
      </label>
      <div className="review-actions">
        <button
          className="btn primary"
          disabled={busy || !reason.trim()}
          onClick={() => decide("CONFIRMED")}
        >
          <Check size={14} />
          Подтвердить
        </button>
        <button
          className="btn"
          disabled={busy || !reason.trim()}
          onClick={() => decide("REJECTED")}
        >
          <X size={14} />
          Отклонить
        </button>
      </div>
    </article>
  );
}
