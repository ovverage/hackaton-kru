import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Monitor,
  Play,
  Search,
  X,
  ClipboardCheck,
  LockKeyhole,
  Wifi,
  Video,
} from "lucide-react";
import ControlStatus from "./ControlStatus";
import { pendingEvents, sessionSummary } from "./sessionStatus";
import {
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
  lockedOnly: boolean;
  busy: boolean;
  onExam: (id: string) => void;
  onDevice: (id: string) => void;
  onEvent: (id: string) => void;
  onStart: () => void;
  onEnd: () => Promise<boolean>;
};

export default function Classroom(p: Props) {
  const summary = sessionSummary(p.devices, p.events);
  const reviewQueue = pendingEvents(p.events);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [ending, setEnding] = useState(false);
  const filtered = p.devices.filter(
    (d) =>
      (p.lockedOnly
        ? d.state.access === "LOCKED"
        : d.state.access !== "LOCKED") &&
      `${d.student} ${d.name}`.toLowerCase().includes(query.toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 8));
  const currentPage = Math.min(page, pages - 1);
  useEffect(() => {
    setPage(0);
    setQuery("");
  }, [p.exam.id, p.lockedOnly]);
  return (
    <>
      <section className="session-overview" aria-label="Сводка сеанса">
        {[
          {
            icon: Wifi,
            value: summary.online,
            label: "На связи",
            detail: `из ${p.devices.length} компьютеров`,
            tone: "blue",
          },
          {
            icon: Play,
            value: summary.running,
            label: "Выполняют тест",
            detail: "онлайн, доступ открыт",
            tone: "green",
          },
          {
            icon: LockKeyhole,
            value: summary.locked,
            label: "Заблокированы",
            detail: "нужно решение преподавателя",
            tone: summary.locked ? "red" : "neutral",
          },
          {
            icon: ClipboardCheck,
            value: summary.pending,
            label: "Ожидают проверки",
            detail: "события текущего сеанса",
            tone: summary.pending ? "amber" : "neutral",
          },
        ].map((item) => (
          <article className={`overview-card ${item.tone}`} key={item.label}>
            <div className="overview-label">
              <span>{item.label}</span>
              <item.icon size={18} aria-hidden="true" />
            </div>
            <strong>{item.value}</strong>
            <small>{item.detail}</small>
          </article>
        ))}
      </section>
      <section className="class-session" aria-label="Текущий сеанс">
        <span className="session-status">
          <i
            className={`status-dot ${p.exam.status === "RUNNING" ? "green" : "blue"}`}
          />
          {p.exam.status === "RUNNING"
            ? "Тест идёт"
            : p.exam.status === "COMPLETED"
              ? "Сеанс завершён"
              : "Готов к запуску"}
        </span>
        <span className="session-environment">
          <Monitor size={18} />
          {p.devices.length} компьютеров
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
        {p.devices.some((d) => d.online && d.state.lifecycle === "READY") && (
          <button className="btn primary" disabled={p.busy} onClick={p.onStart}>
            <Play size={15} />
            Начать тест
          </button>
        )}
        {Object.values(p.exam.participants).some(
          (d) => d.state.lifecycle !== "COMPLETED",
        ) && (
          <button
            className="btn"
            disabled={p.busy}
            onClick={() => setEnding(true)}
          >
            Завершить сеанс
          </button>
        )}
      </section>
      <section className="class-roster session-roster">
        <div className="roster-toolbar">
          <h2>
            {p.lockedOnly ? "Заблокированные" : "Текущие компьютеры"}{" "}
            <span className="count">{filtered.length}</span>
          </h2>
          <div className="roster-search">
            <Search size={16} />
            <input
              aria-label="Поиск компьютера"
              placeholder="Найти компьютер или ученика"
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
                <th>Компьютер / ученик</th>
                <th>Состояние</th>
                <th>Контроль</th>
                <th>
                  {p.lockedOnly ? "Причина блокировки" : "Последнее событие"}
                </th>
                <th>Записи</th>
                <th>
                  <span className="sr-only">Открыть</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered
                .slice(currentPage * 8, currentPage * 8 + 8)
                .map((d, i) => {
                  const events = p.events
                    .filter((e) => e.device_id === d.id)
                    .sort((a, b) => b.created_at - a.created_at);
                  const locked = d.state.access === "LOCKED";
                  const reason = locked ? d.state.reason : events[0]?.type;
                  return (
                    <tr key={d.id}>
                      <td>
                        <button
                          className="roster-student"
                          onClick={() => p.onDevice(d.id)}
                        >
                          <span className={`student-initials shade-${i % 4}`}>
                            <Monitor size={19} />
                          </span>
                          <span>
                            <strong>{d.name}</strong>
                            <small>
                              {d.student !== d.name
                                ? d.student
                                : "Участник сеанса"}
                            </small>
                          </span>
                        </button>
                      </td>
                      <td>
                        <span className="roster-status">
                          <i
                            className={`status-dot ${locked ? "red" : !d.online ? "amber" : "green"}`}
                          />
                          {locked
                            ? "Заблокирован"
                            : !d.online
                              ? "Нет связи"
                              : d.state.lifecycle === "RUNNING"
                                ? "Выполняет тест"
                                : d.state.lifecycle === "COMPLETED"
                                  ? "Завершил"
                                  : "Ожидает старта"}
                        </span>
                        {locked && !d.online && (
                          <small>Нет связи с компьютером</small>
                        )}
                      </td>
                      <td>
                        <ControlStatus device={d} />
                      </td>
                      <td>
                        {reason ? eventNames[reason] || reason : "Нет событий"}
                        {events[0] && (
                          <small>{clock(events[0].created_at)}</small>
                        )}
                      </td>
                      <td>
                        {events.reduce((n, e) => n + e.media.length, 0)} видео
                      </td>
                      <td>
                        <button
                          className="btn small"
                          onClick={() => p.onDevice(d.id)}
                        >
                          Открыть <ChevronRight size={15} />
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
              ? "Компьютер не найден"
              : p.lockedOnly
                ? "Заблокированных компьютеров нет"
                : "Текущих компьютеров нет"}
          </div>
        )}
        <footer className="roster-footer">
          <span>
            Показано{" "}
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
      <section
        className="session-review"
        aria-labelledby="session-review-title"
      >
        <header>
          <div>
            <h2 id="session-review-title">
              Требуют внимания <span className="count">{summary.pending}</span>
            </h2>
            <p>Откройте запись и проверьте контекст события.</p>
          </div>
          <ClipboardCheck size={22} aria-hidden="true" />
        </header>
        {reviewQueue.length ? (
          <div className="session-review-list">
            {reviewQueue.map((event) => (
              <button
                className="session-review-item"
                key={event.id}
                onClick={() => p.onEvent(event.id)}
              >
                <span className="review-item-icon">
                  <Video size={18} aria-hidden="true" />
                </span>
                <span className="review-item-main">
                  <strong>{eventNames[event.type] || event.type}</strong>
                  <small>
                    {event.student} · {event.device_name}
                  </small>
                </span>
                <span className="review-item-meta">
                  <time
                    dateTime={new Date(event.created_at * 1000).toISOString()}
                  >
                    {clock(event.created_at)}
                  </time>
                  <small>
                    {event.media.length
                      ? "Видео доступно"
                      : event.media_expired_at
                        ? "Срок записи истёк"
                        : "Без записи"}
                  </small>
                </span>
                <ChevronRight size={17} aria-hidden="true" />
              </button>
            ))}
          </div>
        ) : (
          <div className="review-queue-empty">
            <ClipboardCheck size={21} />
            <span>Все события проверены. Новые появятся здесь.</span>
          </div>
        )}
        {summary.pending > reviewQueue.length && (
          <p className="review-queue-note">
            Показаны {reviewQueue.length} последних событий. После решения
            появятся следующие; все записи также доступны в карточках
            компьютеров.
          </p>
        )}
      </section>
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
                Контроль завершится на всех компьютерах. События и записи
                сохранятся.
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
                    if (await p.onEnd()) setEnding(false);
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
