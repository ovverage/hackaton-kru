import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, ClipboardCheck, Play, Search, Video, X } from "lucide-react";
import { Bubble, RegMarks, Sheet } from "./components/Design";
import { pendingEvents, preparationIssue, sessionSummary, type TestEnvironment } from "./sessionStatus";
import { clock, eventNames, type Device, type Exam, type Incident } from "./types";

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

const SEATS_PER_ROW = 6;
const AISLE_AFTER = 3;

function seatNumber(device: Device, index: number) {
  const match = device.name.match(/(\d+)\D*$/);
  return match ? match[1].padStart(2, "0") : String(index + 1).padStart(2, "0");
}

function bubbleState(device: Device): "on" | "pause" | "off" | "" {
  if (!device.online) return "off";
  if (device.state.access === "LOCKED") return "pause";
  return device.state.lifecycle === "RUNNING" ? "on" : "";
}

function DistractionCounters({ device }: { device: Device }) {
  return (
    <span className="cnts" aria-label={`Отвлечения: вниз ${device.state.counts.DOWN}, влево ${device.state.counts.LEFT}, вправо ${device.state.counts.RIGHT}`}>
      {(["DOWN", "LEFT", "RIGHT"] as const).map((direction) => (
        <span className="cnt" key={direction} title={`${direction}: ${device.state.counts[direction]} из 3`}>
          <span className="counter-arrow">{direction === "DOWN" ? "↓" : direction === "LEFT" ? "←" : "→"}</span>
          {[1, 2, 3].map((mark) => <Bubble key={mark} size="xs" state={device.state.counts[direction] >= mark ? (mark === 3 ? "pause" : "on") : ""} />)}
        </span>
      ))}
    </span>
  );
}

export default function Classroom(p: Props) {
  const summary = sessionSummary(p.devices, p.events);
  const reviewQueue = pendingEvents(p.events);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [ending, setEnding] = useState(false);
  const environment: TestEnvironment = p.exam.environment.kind === "BROWSER" ? "BROWSER" : "WINDOW";
  const sorted = [...p.devices].sort((a, b) => a.name.localeCompare(b.name, "ru", { numeric: true }));
  const filtered = sorted.filter((device) =>
    (p.lockedOnly ? device.state.access === "LOCKED" : true) &&
    `${device.student} ${device.name}`.toLowerCase().includes(query.toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 8));
  const currentPage = Math.min(page, pages - 1);
  const rows = Array.from({ length: Math.ceil(sorted.length / SEATS_PER_ROW) }, (_, index) => sorted.slice(index * SEATS_PER_ROW, (index + 1) * SEATS_PER_ROW));
  const ready = p.devices.filter((device) => device.online && device.state.lifecycle === "READY").length;
  const offline = p.devices.filter((device) => !device.online).length;

  useEffect(() => {
    setPage(0);
    setQuery("");
  }, [p.exam.id, p.lockedOnly]);

  return (
    <>
      <section className="class-session session-controls" aria-label="Текущий сеанс">
        <label className="select session-select">
          <span className="sr-only">Выбрать сеанс</span>
          <select className="input" value={p.exam.id} onChange={(event) => p.onExam(event.target.value)}>
            {p.exams.map((exam) => <option key={exam.id} value={exam.id}>{exam.title}</option>)}
          </select>
        </label>
        {ready > 0 && <button className="btn primary" disabled={p.busy} onClick={p.onStart}><Play size={15} />Начать на {ready} {ready === 1 ? "компьютере" : "компьютерах"}</button>}
        {Object.values(p.exam.participants).some((device) => device.state.lifecycle !== "COMPLETED") && <button className="btn" disabled={p.busy} onClick={() => setEnding(true)}>Завершить сеанс</button>}
      </section>

      <Sheet className="room-plan-sheet">
        <RegMarks />
        <div className="plan-wrap">
          <div className="plan">
            <div className="desk-row"><div className="desk">Доска и стол преподавателя</div></div>
            {rows.map((row, rowIndex) => (
              <div className="plan-row" key={rowIndex}>
                <span className="row-mark">ряд {rowIndex + 1}</span>
                {Array.from({ length: SEATS_PER_ROW }, (_, column) => {
                  const device = row[column];
                  const globalIndex = rowIndex * SEATS_PER_ROW + column;
                  return (
                    <span className={`seat-slot ${column === AISLE_AFTER ? "after-aisle" : ""}`} key={column}>
                      {device ? (
                        <button className={`seat ${device.state.access === "LOCKED" ? "seat-paused" : ""}`} onClick={() => p.onDevice(device.id)} title={`${device.name}: ${device.student}`}>
                          <Bubble size="lg" state={bubbleState(device)} flag={p.events.some((event) => event.device_id === device.id && event.decision === "PENDING")} />
                          {seatNumber(device, globalIndex)}
                        </button>
                      ) : <span />}
                    </span>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="tally" aria-label="Итог по аудитории">
            <div className="tally-row"><b>{summary.running}</b><span><Bubble state="on" />пишут тест</span></div>
            <div className="tally-row tally-danger"><b>{summary.locked}</b><span><Bubble state="pause" />на паузе, ждут вас</span></div>
            <div className="tally-row"><b>{ready}</b><span><Bubble />ждут старта</span></div>
            <div className="tally-row muted"><b>{offline}</b><span><Bubble state="off" />нет связи</span></div>
            <button className="tally-row sep tally-events" onClick={() => reviewQueue[0] && p.onEvent(reviewQueue[0].id)}><b>{summary.pending}</b><span><Bubble state="warn" />событий ждут решения</span></button>
          </div>
        </div>
      </Sheet>

      <div className="room-lower">
        <section className="panel class-roster session-roster">
          <div className="roster-toolbar">
            <h2 className="t-h2">{p.lockedOnly ? "На паузе" : "Пишут тест"} <span className="muted">{filtered.length}</span></h2>
            <label className="roster-search"><Search size={15} /><input aria-label="Поиск компьютера или ученика" placeholder="Компьютер или ученик" value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }} /></label>
          </div>
          {summary.locked > 0 && !p.lockedOnly && <button className="paused-callout" onClick={() => { const paused = sorted.find((device) => device.state.access === "LOCKED"); if (paused) p.onDevice(paused.id); }}><span>{summary.locked} компьютера на паузе ждут вашего решения</span><ChevronRight size={16} /></button>}
          <div className="table-box roster-scroll">
            <table className="table dense roster-table">
              <thead><tr><th>Компьютер</th><th>Отвлечения</th><th>{p.lockedOnly ? "Причина паузы" : "Последнее событие"}</th><th><span className="sr-only">Открыть</span></th></tr></thead>
              <tbody>
                {filtered.slice(currentPage * 8, currentPage * 8 + 8).map((device) => {
                  const ownEvents = p.events.filter((event) => event.device_id === device.id).sort((a, b) => b.created_at - a.created_at);
                  const issue = preparationIssue(device, environment);
                  const latest = ownEvents[0];
                  return (
                    <tr key={device.id} className={device.state.access === "LOCKED" ? "paused" : ""}>
                      <td><button className="who roster-student" onClick={() => p.onDevice(device.id)}><Bubble state={bubbleState(device)} flag={ownEvents.some((event) => event.decision === "PENDING")} /><span><strong>{seatNumber(device, sorted.indexOf(device))} {device.student}</strong><small>{device.name}, {device.state.access === "LOCKED" ? "тест на паузе" : !device.online ? "нет связи" : device.state.lifecycle === "RUNNING" ? "пишет тест" : "ждёт старта"}</small>{issue && <small className="todo">{issue}</small>}</span></button></td>
                      <td>{device.online ? <DistractionCounters device={device} /> : <span className="muted">Нет данных</span>}</td>
                      <td>{device.state.access === "LOCKED" ? eventNames[device.state.reason || ""] || "Тест на паузе" : latest ? eventNames[latest.type] || latest.type : <span className="muted">Событий нет</span>}{latest && <small className={latest.decision === "PENDING" ? "todo" : ""}>{clock(latest.created_at)}{latest.decision === "PENDING" ? ", ждёт решения" : latest.media.length ? `, ${latest.media.length} видео` : ""}</small>}</td>
                      <td><button className="icon-btn" aria-label={`Открыть ${device.name}`} onClick={() => p.onDevice(device.id)}><ChevronRight size={17} /></button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {!filtered.length && <div className="empty">{query ? "Компьютер не найден" : p.lockedOnly ? "Компьютеров на паузе нет" : "Текущих компьютеров нет"}</div>}
          <footer className="roster-footer"><span>Показано {Math.min(8, Math.max(0, filtered.length - currentPage * 8))} из {filtered.length}</span><div className="pagination"><button aria-label="Предыдущая страница" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={15} /></button><span>{currentPage + 1} из {pages}</span><button aria-label="Следующая страница" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}><ChevronRight size={15} /></button></div></footer>
        </section>

        <aside className="session-review" aria-labelledby="session-review-title">
          <header><h2 id="session-review-title" className="t-h2">Ждут решения <span className="review-count">{summary.pending}</span></h2></header>
          {reviewQueue.length ? <div className="session-review-list">{reviewQueue.map((event) => (
            <button className="session-review-item" key={event.id} onClick={() => p.onEvent(event.id)}>
              <span className="review-thumb"><Video size={20} /></span>
              <span className="review-item-main"><strong>{eventNames[event.type] || event.type}</strong><small>{event.student} · {event.device_name}</small><small>{clock(event.created_at)}{event.media.length ? ", видео доступно" : ""}</small></span>
              <ChevronRight size={17} />
            </button>
          ))}</div> : <div className="review-queue-empty"><ClipboardCheck size={21} /><span>Все события проверены.</span></div>}
        </aside>
      </div>

      {ending && <div className="overlay"><section className="modal" role="dialog" aria-modal="true" aria-label="Завершить сеанс"><header className="modal-head"><div><h2>Завершить сеанс?</h2><p>Контроль завершится на всех компьютерах.</p></div><button className="icon-btn" aria-label="Закрыть" disabled={p.busy} onClick={() => setEnding(false)}><X size={19} /></button></header><div className="modal-body"><p>События, записи и решения останутся в отчёте.</p><div className="button-row end"><button className="btn" disabled={p.busy} onClick={() => setEnding(false)}>Отмена</button><button className="btn primary" disabled={p.busy} onClick={async () => { if (await p.onEnd()) setEnding(false); }}>Завершить сеанс</button></div></div></section></div>}
    </>
  );
}
