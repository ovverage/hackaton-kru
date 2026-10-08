import Classroom from "./Classroom";
import TeacherFaces from "./TeacherFaces";
import {
  Bubble,
  Clock,
  Logo,
  NavItem,
  MiniBar,
  Pill,
  Tabs,
  RegMarks,
  StepBubble,
} from "./components/Design";
import {
  controlSignals,
  pendingEvents,
  preparationIssue,
  type TestEnvironment,
} from "./sessionStatus";
import { useDialogFocus } from "./useDialogFocus";
import {
  endExam,
  sendDeviceCommand,
  sendGroupCommand,
  type CommandType,
} from "./commands";
import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type FormEvent,
} from "react";
import {
  ShieldCheck,
  LayoutGrid,
  Monitor,
  ClipboardCheck,
  History,
  SlidersHorizontal,
  Plus,
  ArrowUpRight,
  ChevronRight,
  X,
  Play,
  LockKeyhole,
  UnlockKeyhole,
  Check,
  Eye,
  Smartphone,
  UsersRound,
  Pause,
  Globe,
  AppWindow,
  EyeOff,
  MicOff,
  Camera,
  Trash2,
  ChevronLeft,
  ChevronDown,
  Download,
  LogOut,
  Video,
  LoaderCircle,
  CheckCheck,
} from "lucide-react";
import {
  api,
  gazeEnabled,
  clock,
  eventNames,
  decisionNames,
  type Snapshot,
  type Device,
  type Exam,
  type Incident,
} from "./types";
const empty: Snapshot = { devices: [], exams: [], events: [], commands: [] };
const directions = [
  ["DOWN", "Вниз"],
  ["LEFT", "Влево"],
  ["RIGHT", "Вправо"],
] as const;
function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={"badge " + tone}>{children}</span>;
}
function Modal({
  title,
  subtitle,
  children,
  onClose,
  wide = false,
  className = "",
  headerExtra,
  avatar,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  className?: string;
  headerExtra?: ReactNode;
  avatar?: string;
}) {
  const dialog = useRef<HTMLElement>(null);
  useDialogFocus(dialog, onClose);
  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        ref={dialog}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`modal ${wide ? "wide" : ""} ${className}`}
      >
        <header className="modal-head">
          {avatar && (
            <span className="modal-avatar" aria-hidden="true">
              {avatar}
            </span>
          )}
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          {headerExtra}
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
function Auth({
  setup,
  onLogin,
}: {
  setup: boolean;
  onLogin: (u: { name: string }) => void;
}) {
  const [name, setName] = useState(""),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [visible, setVisible] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(
        (await api("/auth/" + (setup ? "setup" : "login"), { name, password }))
          .user,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="q auth-layout redesign-auth">
      <section className="auth-story">
        <Logo onDark />
        <div className="auth-message">
          <h1>
            Вся аудитория
            <br />
            на одном экране
          </h1>
          <p>
            Qorgau замечает телефон, второе лицо и долгий взгляд в сторону. Вы
            получаете короткую запись и решаете, было ли нарушение.
          </p>
          <div className="auth-example" aria-hidden="true">
            <div className="auth-example-head">
              <Bubble state="pause" /> Тест на паузе <span>09:52</span>
            </div>
            <div className="auth-example-body">
              <div className="cam">
                <Video size={38} />
                <RegMarks light />
              </div>
              <div>
                <strong>Телефон в кадре</strong>
                <p>Айгерим Нурланова</p>
                <small>K301-PC14</small>
                <Pill tone="warn">Ждёт вашего решения</Pill>
              </div>
            </div>
            <div className="auth-example-foot">
              Посмотреть запись <ChevronRight size={16} />
            </div>
          </div>
          <div className="auth-facts">
            <span>
              <Video size={18} />
              Видео только вокруг события
            </span>
            <span>
              <MicOff size={18} />
              Звук не записывается
            </span>
            <span>
              <ShieldCheck size={18} />
              Решение за преподавателем
            </span>
          </div>
        </div>
        <footer>Команда PEEP. Кейс КРУ и Qostanai Hub, 2026</footer>
      </section>
      <main className="auth-form">
        <div className="auth-card">
          <h2>{setup ? "Создать кабинет" : "Вход в кабинет"}</h2>
          <p>
            {setup
              ? "Создайте учётную запись преподавателя для этой установки Qorgau."
              : "Войдите, чтобы увидеть аудиторию и продолжить работу."}
          </p>
          <form onSubmit={submit}>
            <label className="field">
              Имя преподавателя
              <input
                className="input"
                autoFocus
                required
                minLength={2}
                maxLength={80}
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="username"
                placeholder="Например, Айгуль Сапарова"
              />
            </label>
            <label className="field">
              {setup ? "Придумайте пароль" : "Пароль"}
              <span className="password-field">
                <input
                  className="input"
                  required
                  type={visible ? "text" : "password"}
                  minLength={8}
                  maxLength={128}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={setup ? "new-password" : "current-password"}
                  placeholder="Не меньше 8 символов"
                />
                <button
                  type="button"
                  className="icon-btn"
                  aria-label={visible ? "Скрыть пароль" : "Показать пароль"}
                  onClick={() => setVisible(!visible)}
                >
                  {visible ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </span>
            </label>
            {error && (
              <div className="error" role="alert">
                {error}
              </div>
            )}
            <button className="btn primary block" disabled={busy}>
              {busy && <LoaderCircle className="spin" size={18} />}
              {setup ? "Создать кабинет" : "Войти"}
            </button>
          </form>
          <div className="auth-protection">
            <LockKeyhole size={20} />
            <div>
              <strong>Вход защищён</strong>
              <p>
                После 5 неверных попыток — пауза на минуту. Вход действует 12
                часов.
              </p>
            </div>
          </div>
          <p className="fine">
            Видео и решения хранятся на сервере этой установки Qorgau. Камеры
            работают только на компьютерах с открытым Qorgau.
          </p>
        </div>
      </main>
    </div>
  );
}
export default function App() {
  const [auth, setAuth] = useState<{
      setup_required: boolean;
      user: { name: string } | null;
    } | null>(null),
    [data, setData] = useState<Snapshot>(empty),
    [page, setPage] = useState("room"),
    [connected, setConnected] = useState(false),
    [fatal, setFatal] = useState(""),
    [toast, setToast] = useState(""),
    [busy, setBusy] = useState(false);
  const [examId, setExamId] = useState(""),
    [newExam, setNewExam] = useState(false),
    [deviceId, setDeviceId] = useState<string | null>(null),
    [eventId, setEventId] = useState<string | null>(null);
  const [action, setAction] = useState<{
      device: Device;
      type: CommandType | "REVOKE";
    } | null>(null),
    [reason, setReason] = useState("");
  const [receivedAt, setReceivedAt] = useState(0),
    [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    import("./dev/fixtures").then(({ fixtureView }) => {
      const view = fixtureView();
      setPage(view.page);
      if (view.modal === "new") setNewExam(true);
      if (view.modal === "device") setDeviceId("device-14");
      if (view.modal === "event") setEventId("event-1");
    });
  }, []);
  function receive(snapshot: Snapshot) {
    setData({
      ...snapshot,
      devices: snapshot.devices.filter((d) => !d.simulated && !d.revoked_at),
      exams: snapshot.exams.filter((e) => !e.simulated),
      events: snapshot.events.filter((e) => !e.simulated),
    });
    setReceivedAt(Date.now());
  }
  const liveDevices =
    connected && now - receivedAt < 6000
      ? data.devices.filter((d) => d.online)
      : [];
  const user = auth?.user;
  const exam =
    data.exams.find((x) => x.id === examId) ||
    data.exams.find((x) => x.status !== "COMPLETED");
  const devices = exam
    ? data.devices
        .filter((d) => d.exam_id === exam.id)
        .map((d) => ({
          ...d,
          online: liveDevices.some((live) => live.id === d.id),
        }))
    : [];
  const sessionActive = Boolean(exam && exam.status !== "COMPLETED");
  useEffect(() => {
    if (
      sessionActive &&
      !["room", "blocked", "teachers", "review"].includes(page)
    )
      setPage("room");
    if (!sessionActive && page === "blocked") setPage("room");
  }, [sessionActive, page]);
  const events = exam ? data.events.filter((e) => e.exam_id === exam.id) : [];
  const selectedDevice =
    devices.find((d) => d.id === deviceId) ||
    data.devices.find((d) => d.id === deviceId);
  const selectedEvent = data.events.find((e) => e.id === eventId);
  const pending = pendingEvents(data.events, Infinity);
  const queue = pendingEvents(
    selectedEvent
      ? data.events.filter((e) => e.exam_id === selectedEvent.exam_id)
      : events,
    Infinity,
  );
  async function refresh() {
    receive(await api<Snapshot>("/snapshot"));
  }
  useEffect(() => {
    api("/auth/status")
      .then(setAuth)
      .catch((e) => setFatal(e.message));
  }, []);
  useEffect(() => {
    if (!user) return;
    if (
      import.meta.env.DEV &&
      new URLSearchParams(location.search).has("fixture")
    ) {
      refresh()
        .then(() => setConnected(true))
        .catch((e) => setToast(e.message));
      const fixtureTimer = window.setInterval(() => {
        refresh().catch((e) => setToast(e.message));
      }, 2000);
      return () => window.clearInterval(fixtureTimer);
    }
    let stopped = false;
    let socket: WebSocket;
    let timer: number;
    const connect = () => {
      socket = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/teacher`,
      );
      socket.onopen = () => setConnected(true);
      socket.onmessage = (e) => receive(JSON.parse(e.data));
      socket.onclose = () => {
        setConnected(false);
        if (!stopped) timer = window.setTimeout(connect, 2000);
      };
      socket.onerror = () => socket.close();
    };
    refresh().catch((e) => setToast(e.message));
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, [user?.name]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 6000);
    return () => clearTimeout(timer);
  }, [toast]);
  async function run(fn: () => Promise<unknown>, message?: string) {
    setBusy(true);
    try {
      await fn();
      await refresh();
      if (message) setToast(message);
      return true;
    } catch (e) {
      setToast((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function command(d: Device, type: CommandType, why = "") {
    await sendDeviceCommand(d, type, why, receive);
  }
  function ask(d: Device, type: CommandType | "REVOKE") {
    setAction({ device: d, type });
    setReason("");
  }
  if (fatal)
    return (
      <div className="loading error">
        Сервер недоступен. {fatal}
        <button className="btn" onClick={() => location.reload()}>
          Повторить
        </button>
      </div>
    );
  if (!auth)
    return (
      <div className="loading">
        <LoaderCircle className="spin" /> Загружаем кабинет…
      </div>
    );
  if (!user)
    return (
      <Auth
        setup={auth.setup_required}
        onLogin={(u) => setAuth({ setup_required: false, user: u })}
      />
    );
  const nav = sessionActive
    ? [
        { id: "room", name: "Аудитория", icon: LayoutGrid },
        { id: "blocked", name: "На паузе", icon: Pause },
        { id: "teachers", name: "Преподаватели", icon: UsersRound },
      ]
    : [
        { id: "room", name: "Аудитория", icon: LayoutGrid },
        { id: "review", name: "События", icon: ClipboardCheck },
        { id: "history", name: "Сеансы и отчёты", icon: History },
        { id: "devices", name: "Компьютеры", icon: Monitor },
        { id: "rules", name: "Правила контроля", icon: SlidersHorizontal },
        { id: "teachers", name: "Преподаватели", icon: UsersRound },
      ];
  return (
    <div className="q shell app">
      <aside className="side sidebar">
        <button
          className="brand-action"
          onClick={(e) => {
            e.preventDefault();
            setPage("room");
          }}
        >
          <Logo onDark />
        </button>
        <nav className="nav" aria-label="Разделы">
          {nav.map((n) => (
            <button
              key={n.id}
              aria-label={n.name}
              title={n.name}
              onClick={() => setPage(n.id)}
              className={page === n.id ? "active" : ""}
            >
              <NavItem
                icon={n.icon}
                tone={n.id === "blocked" ? "red" : "amber"}
                badge={
                  n.id === "blocked"
                    ? devices.filter((d) => d.state.access === "LOCKED").length
                    : n.id === "review"
                      ? pending.length
                      : 0
                }
              >
                {n.name}
              </NavItem>
            </button>
          ))}
        </nav>
        <div className="side-note sidebar-tip">
          <strong>Решение за вами</strong>
          <p>
            Система отмечает события. Спорные моменты проверяет преподаватель.
          </p>
        </div>
        <div className="me profile">
          <div className="avatar">{user.name.slice(0, 1)}</div>
          <div>
            <strong>{user.name}</strong>
            <span>Преподаватель</span>
          </div>
          <button
            className="icon-btn"
            aria-label="Выйти"
            onClick={async () => {
              await api("/auth/logout", {});
              setAuth({ ...auth, user: null });
              setData(empty);
            }}
          >
            <LogOut size={18} />
          </button>
        </div>
      </aside>
      <div className="content workspace">
        <header className="topbar">
          <Clock since={sessionActive ? exam?.created_at : undefined} />
          <div className="link-status connection">
            <Bubble state={connected ? "ink" : "warn"} size="xs" />
            <span>
              {connected ? "Сервер на связи" : "Восстанавливаем связь"}
            </span>
          </div>
        </header>
        <main className="main">
          <div className="page-head page-heading">
            <div>
              <h1 className="t-h1">
                {page === "blocked"
                  ? "На паузе"
                  : page === "room"
                    ? sessionActive
                      ? exam?.title || "Аудитория"
                      : "Аудитория"
                    : page === "review"
                      ? "События"
                      : page === "history"
                        ? "Сеансы и отчёты"
                        : page === "devices"
                          ? "Компьютеры"
                          : page === "teachers"
                            ? "Преподаватели"
                            : "Правила контроля"}
              </h1>
              <p>
                {["room", "blocked"].includes(page)
                  ? exam
                    ? `Группа ${exam.group}, аудитория ${exam.room}. ${sessionActive ? "Тест идёт с " + shortClock(exam.created_at) + (exam.environment.kind === "BROWSER" ? " в Qorgau Browser" : " в программе на компьютере") : "Завершённый сеанс"}`
                    : "Подготовьте компьютеры, выберите тест и начните сеанс."
                  : page === "review"
                    ? "Посмотрите запись и примите решение по каждому событию."
                    : page === "history"
                      ? "События, решения и отчёты по проведённым тестам."
                      : page === "devices"
                        ? "Только те, где Qorgau открыт прямо сейчас. Подготовьте их перед тестом."
                        : page === "teachers"
                          ? "Лица преподавателей для продолжения теста на компьютере ученика."
                          : "Что замечает Qorgau, когда ставит тест на паузу и что решаете вы."}
              </p>
            </div>
            {!sessionActive &&
              ["room", "history", "devices"].includes(page) && (
                <button
                  className="btn primary"
                  onClick={() => setNewExam(true)}
                >
                  <Plus size={18} /> Новый сеанс
                </button>
              )}
            {page === "room" && !sessionActive && (
              <a className="btn" href="/api/student/download">
                <Download size={18} /> Скачать Qorgau для учеников
              </a>
            )}
          </div>
          {["room", "blocked"].includes(page) && (
            <>
              {!exam ? (
                <BeforeRoom
                  devices={liveDevices}
                  exams={data.exams}
                  events={data.events}
                  onDevices={() => setPage("devices")}
                  onHistory={() => setPage("history")}
                  onNew={() => setNewExam(true)}
                  onReview={setEventId}
                />
              ) : (
                <Classroom
                  exam={exam}
                  exams={data.exams}
                  devices={devices}
                  events={events}
                  busy={busy}
                  onExam={setExamId}
                  onDevice={setDeviceId}
                  onEvent={setEventId}
                  onUnlock={(id) => {
                    const d = devices.find((device) => device.id === id);
                    if (d) ask(d, "UNLOCK");
                  }}
                  onReview={() => setPage("review")}
                  lockedOnly={page === "blocked"}
                  onStart={() =>
                    run(
                      () =>
                        sendGroupCommand(
                          devices.filter(
                            (d) => d.online && d.state.lifecycle === "READY",
                          ),
                          "START",
                          "",
                          receive,
                        ),
                      "Компьютеры подтвердили начало теста",
                    )
                  }
                  onEnd={() =>
                    run(
                      () => endExam(exam, receive),
                      "Компьютеры подтвердили завершение сеанса",
                    )
                  }
                />
              )}
            </>
          )}
          {page === "review" && (
            <EventsPage
              events={data.events}
              exams={data.exams}
              onSelect={setEventId}
            />
          )}
          {page === "history" && (
            <HistoryPage
              exams={data.exams}
              events={data.events}
              onOpen={(id) => {
                setExamId(id);
                setPage("room");
              }}
              onReview={setEventId}
            />
          )}
          {page === "devices" && (
            <Computers
              devices={liveDevices}
              busy={busy}
              onRevoke={(d) => ask(d, "REVOKE")}
            />
          )}
          {page === "teachers" && <TeacherFaces />}
          {page === "rules" && (
            <>
              <div className="legend rules-legend">
                <Pill tone="pause">
                  <Bubble state="pause" size="xs" />
                  Тест на паузе
                </Pill>
                <Pill tone="warn">
                  <Bubble state="warn" size="xs" />
                  Ждёт решения, тест идёт
                </Pill>
              </div>
              <div className="panel rules-sheet">
                <section className="rule-group">
                  <h2>
                    <Bubble state="pause" />
                    Ставят тест на паузу
                  </h2>
                  <Rule
                    number={1}
                    title="Три отметки за взгляд в одну сторону"
                    text="Взгляд вниз, влево или вправо 5 секунд и дольше даёт одну отметку. Стороны считаются отдельно; третья отметка ставит тест на паузу."
                  />
                  <Rule
                    number={2}
                    title="Телефон в кадре"
                    text="Два уверенных обнаружения подряд сразу ставят тест на паузу, без отметок. Телефон в кадре ещё не доказывает, что ученик фотографировал."
                  />
                  <Rule
                    number={3}
                    title="Лица не видно"
                    text="После 10 секунд тест ставится на паузу по технической причине: ученик мог отойти или заслонить камеру."
                  />
                  <Rule
                    number={4}
                    title="Сбой окружения"
                    text="Окно теста закрыто, нет связи с сервером, изменился экран, открыт удалённый рабочий стол или камера замерла."
                  />
                </section>
                <section className="rule-group">
                  <h2>
                    <Bubble state="warn" />
                    Уходят вам на проверку, тест продолжается
                  </h2>
                  <Rule
                    number={5}
                    title="Второе лицо в кадре"
                    text="Рядом с учеником секунду и дольше видно ещё одно лицо."
                  />
                  <Rule
                    number={6}
                    title="Частые короткие отвлечения"
                    text="Три коротких взгляда в сторону за минуту, в сумме от 6 секунд. Отметок за взгляд не добавляют."
                  />
                  <Rule
                    number={7}
                    title="Подъём телефона"
                    text="Телефон подняли и держат, возможна съёмка экрана. Куда смотрит его камера и был ли снимок, Qorgau не определяет."
                  />
                </section>
                <section className="rule-group">
                  <h2>
                    <Bubble state="ink" />
                    После паузы
                  </h2>
                  <Rule
                    number={8}
                    title="Продолжает только преподаватель"
                    text="Взгляд на экран и решение «Нарушения нет» паузу не снимают. Вы продолжаете тест в кабинете или своим паролем на компьютере ученика. История остаётся в отчёте."
                  />
                  <Rule
                    number={9}
                    title="Внешний тест, отдельный контроль"
                    text="Перед стартом выберите сайт или приложение. Для сайта используется Qorgau Browser, для приложения — выбранное окно программы."
                  />
                </section>
              </div>
            </>
          )}
          <footer className="page-footer">
            <span>
              <ShieldCheck size={14} /> Qorgau · Локальный контроль, осознанные
              решения
            </span>
            <span>Контроль рабочего стола · компьютеры онлайн</span>
          </footer>
        </main>
      </div>
      {newExam && (
        <NewExam
          devices={liveDevices}
          onRules={() => {
            setNewExam(false);
            setPage("rules");
          }}
          onClose={() => setNewExam(false)}
          onCreate={async (body) => {
            const e = await api<Exam>("/exams", body);
            await refresh();
            setExamId(e.id);
            setNewExam(false);
            setPage("room");
          }}
        />
      )}
      {selectedDevice && deviceId && (
        <Modal
          title={selectedDevice.student || selectedDevice.name}
          avatar={(selectedDevice.student || selectedDevice.name)
            .split(" ")
            .slice(0, 2)
            .map((s) => s[0])
            .join("")}
          subtitle={
            selectedDevice.name +
            (exam
              ? (selectedDevice.state.lifecycle === "RUNNING"
                  ? " · пишет с "
                  : " · начало сеанса ") + shortClock(exam.created_at)
              : "")
          }
          className="device-modal"
          onClose={() => setDeviceId(null)}
          wide
        >
          <div className="modal-body">
            <div className="student-status">
              <Badge
                tone={
                  selectedDevice.state.access === "LOCKED" ? "red" : "green"
                }
              >
                {selectedDevice.state.access === "LOCKED"
                  ? "Тест на паузе"
                  : selectedDevice.state.lifecycle === "RUNNING"
                    ? "Пишет тест"
                    : selectedDevice.state.lifecycle === "COMPLETED"
                      ? "Сеанс завершён"
                      : "Ждёт старта"}
              </Badge>
              <span>Круг {selectedDevice.state.epoch}</span>
              {exam?.rule_version && (
                <span className="fine">Правила сеанса {exam.rule_version}</span>
              )}
              {selectedDevice.state.reason && (
                <span>
                  {eventNames[selectedDevice.state.reason] ||
                    "Решение преподавателя"}
                </span>
              )}
            </div>
            <div className="button-row">
              {selectedDevice.exam_id === exam?.id &&
                selectedDevice.state.lifecycle === "READY" && (
                  <button
                    className="btn primary"
                    disabled={busy}
                    onClick={() => run(() => command(selectedDevice, "START"))}
                  >
                    <Play size={16} /> Начать контроль
                  </button>
                )}
              {selectedDevice.exam_id === exam?.id &&
                selectedDevice.state.lifecycle === "RUNNING" && (
                  <>
                    <button
                      className={
                        "btn " +
                        (selectedDevice.state.access === "LOCKED"
                          ? "primary"
                          : "")
                      }
                      disabled={busy}
                      onClick={() =>
                        ask(
                          selectedDevice,
                          selectedDevice.state.access === "LOCKED"
                            ? "UNLOCK"
                            : "LOCK",
                        )
                      }
                    >
                      {selectedDevice.state.access === "LOCKED" ? (
                        <UnlockKeyhole size={16} />
                      ) : (
                        <LockKeyhole size={16} />
                      )}{" "}
                      {selectedDevice.state.access === "LOCKED"
                        ? "Продолжить тест"
                        : "Поставить на паузу"}
                    </button>
                    <button
                      className="btn"
                      onClick={() => ask(selectedDevice, "END_AND_RELEASE")}
                    >
                      Завершить контроль на этом компьютере
                    </button>
                  </>
                )}
            </div>
            <DeviceTimeline
              device={selectedDevice}
              exam={data.exams.find((e) => e.id === selectedDevice.exam_id)}
              events={data.events.filter(
                (e) =>
                  e.device_id === selectedDevice.id &&
                  e.exam_id === selectedDevice.exam_id,
              )}
            />
            <div className="device-columns">
              <section>
                <h3>Контроль на компьютере</h3>
                <div className="device-control">
                  {controlSignals(selectedDevice).map((signal, i) => {
                    const Icon = [Camera, Eye, ShieldCheck][i];
                    return (
                      <div key={signal.label}>
                        <span className="control-tile">
                          <Icon size={20} />
                        </span>
                        <span>
                          <strong>{signal.label}</strong>
                          <small>{signal.detail}</small>
                        </span>
                        <Bubble
                          state={
                            signal.tone === "ready"
                              ? "on"
                              : signal.tone === "attention"
                                ? "warn"
                                : "off"
                          }
                        />
                      </div>
                    );
                  })}
                </div>
                {typeof selectedDevice.capabilities.model_version ===
                  "string" && (
                  <p className="fine device-model-version">
                    Модели: {selectedDevice.capabilities.model_version}
                  </p>
                )}
              </section>
              <section>
                <h3>Отметки за взгляд в этом круге</h3>
                <Counters d={selectedDevice} />
                <p className="fine">
                  5 секунд в сторону — одна отметка. Третья в одну сторону
                  ставит тест на паузу.
                </p>
              </section>
            </div>
            <h3 className="subheading">
              Записи этого компьютера{" "}
              <span className="count">
                {events.filter((e) => e.device_id === selectedDevice.id).length}
              </span>
            </h3>
            <div className="device-evidence">
              {events
                .filter((e) => e.device_id === selectedDevice.id)
                .map((e) => (
                  <article className="device-incident" key={e.id}>
                    <header>
                      <strong>{eventNames[e.type] || e.type}</strong>
                      <time>{clock(e.created_at)}</time>
                    </header>
                    {e.media.length ? (
                      e.media.map((media) => (
                        <div className="cam" key={media.id}>
                          <RegMarks light />
                          {media.mime.startsWith("image/") ? (
                            <img
                              src={media.url}
                              alt={`Кадр: ${eventNames[e.type] || e.type}`}
                            />
                          ) : (
                            <video
                              key={media.id}
                              controls
                              preload="metadata"
                              src={media.url}
                              aria-label={`${eventNames[e.type] || e.type} — ${selectedDevice.name}`}
                              onLoadedMetadata={(v) => {
                                const offset = e.at - (media.clip_start || 0);
                                if (
                                  offset > 0 &&
                                  offset < v.currentTarget.duration
                                )
                                  v.currentTarget.currentTime = offset;
                              }}
                            />
                          )}
                        </div>
                      ))
                    ) : (
                      <div className="empty">
                        <Video size={24} />
                        {e.media_expired_at
                          ? "Срок хранения записи истёк"
                          : "Видео передаётся с компьютера…"}
                      </div>
                    )}
                    <footer>
                      <Badge
                        tone={
                          e.decision === "CONFIRMED"
                            ? "red"
                            : e.decision === "REJECTED"
                              ? "green"
                              : "amber"
                        }
                      >
                        {decisionNames[e.decision]}
                      </Badge>
                      <button
                        className="btn small"
                        onClick={() => setEventId(e.id)}
                      >
                        Разобрать
                      </button>
                    </footer>
                  </article>
                ))}
              {!events.some((e) => e.device_id === selectedDevice.id) && (
                <div className="empty">
                  На этом компьютере пока нет событий.
                </div>
              )}
            </div>
            {data.commands
              .filter(
                (c) =>
                  c.device_id === selectedDevice.id &&
                  ["PENDING", "REJECTED", "EXPIRED"].includes(c.status),
              )
              .slice(0, 3)
              .map((c) => (
                <p className="fine" key={c.id}>
                  Команда{" "}
                  {{
                    START: "Начать контроль",
                    LOCK: "Поставить на паузу",
                    UNLOCK: "Продолжить тест",
                    END_AND_RELEASE: "Завершить контроль на этом компьютере",
                    REVIEW: "Пересмотреть событие",
                  }[c.type] || "Управление сеансом"}
                  :{" "}
                  {c.status === "PENDING"
                    ? "ожидает подтверждения агента"
                    : c.status === "EXPIRED"
                      ? "истёк срок действия"
                      : "не выполнена"}{" "}
                  {c.error}
                </p>
              ))}
          </div>
        </Modal>
      )}
      {selectedEvent && (
        <EventReview
          key={selectedEvent.id}
          event={selectedEvent}
          queue={queue}
          onSelect={setEventId}
          onClose={() => setEventId(null)}
          onUpdate={refresh}
          notify={setToast}
        />
      )}
      {action && (
        <Modal
          title={
            action.type === "REVOKE"
              ? "Отозвать доступ компьютера?"
              : action.type === "UNLOCK"
                ? "Продолжить тест?"
                : action.type === "LOCK"
                  ? "Поставить тест на паузу?"
                  : "Завершить контроль?"
          }
          subtitle={action.device.student + " · " + action.device.name}
          onClose={() => setAction(null)}
        >
          <form
            className="modal-body"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                async () => {
                  if (action.type === "REVOKE")
                    await api(`/devices/${action.device.id}/revoke`, {
                      reason,
                    });
                  else await command(action.device, action.type, reason);
                  setAction(null);
                },
                action.type === "UNLOCK"
                  ? "Тест продолжается. Начался новый круг отметок."
                  : "Решение выполнено",
              );
            }}
          >
            <p>
              {action.type === "REVOKE"
                ? "Сохранённый токен компьютера перестанет работать. История сеансов сохранится; для нового подключения понадобится новая регистрация."
                : action.type === "UNLOCK"
                  ? "Начнётся новый круг трёх счётчиков. Все события и решения сохранятся в истории."
                  : action.type === "END_AND_RELEASE"
                    ? "Контроль этого ученика завершится, блокировка будет снята. Внешний тест автоматически не отправляется."
                    : "Тест будет поставлен на паузу по решению преподавателя. В режиме наблюдения операционная система остаётся доступной."}
            </p>
            <label>
              Причина решения
              <textarea
                autoFocus
                required
                value={reason}
                maxLength={500}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Например: проверено видео, разрешено продолжить"
              />
            </label>
            <div className="button-row end">
              <button
                type="button"
                className="btn"
                onClick={() => setAction(null)}
              >
                Отмена
              </button>
              <button className="btn primary" disabled={busy || !reason.trim()}>
                Подтвердить решение
              </button>
            </div>
          </form>
        </Modal>
      )}
      {toast && (
        <div className="toast" role="status">
          <span>{toast}</span>
          <button
            className="icon-btn"
            onClick={() => setToast("")}
            aria-label="Скрыть уведомление"
          >
            <X size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
const shortClock = (at: number) =>
  new Date(at * 1000).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
const eventAge = (at: number) => {
  const minutes = Math.max(0, Math.floor((Date.now() / 1000 - at) / 60));
  return minutes < 1
    ? "меньше минуты"
    : minutes < 60
      ? `${minutes} мин`
      : `${Math.floor(minutes / 60)} ч`;
};
const eventTotals = (events: Incident[]) => ({
  confirmed: events.filter((e) => e.decision === "CONFIRMED").length,
  rejected: events.filter((e) => e.decision === "REJECTED").length,
  pending: events.filter((e) => e.decision === "PENDING").length,
});
const dateLabel = (at: number) =>
  new Date(at * 1000).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
  });
function QuietNumber({ value }: { value: number }) {
  return value ? <>{value}</> : <span className="zero">—</span>;
}
function Tick({ yes, label }: { yes: boolean; label: string }) {
  return (
    <span
      className={`tick ${yes ? "" : "no"}`}
      role="img"
      aria-label={`${label}: ${yes ? "готово" : "нужно действие"}`}
    >
      {yes && <Check size={13} aria-hidden="true" />}
    </span>
  );
}
function EventThumb({
  event,
  compact = false,
}: {
  event: Incident;
  compact?: boolean;
}) {
  const media = event.media.find((m) => m.url);
  const duration = event.media.reduce(
    (total, m) => total + Math.max(0, (m.clip_end ?? 0) - (m.clip_start ?? 0)),
    0,
  );
  return (
    <div
      className={`cam event-thumb ${compact ? "compact" : ""}`}
      aria-hidden="true"
    >
      {media ? (
        media.mime.startsWith("image/") ? (
          <img src={media.url} alt="" />
        ) : (
          <video src={media.url} preload="metadata" muted />
        )
      ) : (
        <Video size={compact ? 18 : 28} />
      )}
      <RegMarks light />
      {!compact && (
        <span className="play">
          <Play size={18} />
        </span>
      )}
      {duration > 0 && <span className="tc">{duration.toFixed(0)} с</span>}
    </div>
  );
}
function BeforeRoom({
  devices,
  exams,
  events,
  onDevices,
  onHistory,
  onNew,
  onReview,
}: {
  devices: Device[];
  exams: Exam[];
  events: Incident[];
  onDevices: () => void;
  onHistory: () => void;
  onNew: () => void;
  onReview: (id: string) => void;
}) {
  const ready = devices.filter((d) => !preparationIssue(d, "BROWSER"));
  const notReady = devices.filter((d) => preparationIssue(d, "BROWSER"));
  const recent = exams
    .filter((e) => e.status === "COMPLETED")
    .sort((a, b) => b.created_at - a.created_at)
    .slice(0, 3);
  return (
    <>
      <section className="card before-readiness">
        <div className="before-ready">
          <p>Компьютеры аудитории</p>
          <h2>
            <strong>{ready.length}</strong> из {devices.length}
            <br />
            <span>готовы к тесту</span>
          </h2>
          <div className="sbar">
            <span
              style={{
                width: `${devices.length ? (ready.length / devices.length) * 100 : 0}%`,
                background: "var(--navy-800)",
              }}
            />
            <span style={{ flex: 1, background: "var(--amber-fill)" }} />
          </div>
          <div className="legend">
            <span>
              <Bubble state="on" size="xs" />
              {ready.length} готовы
            </span>
            <span>
              <Bubble state="warn" size="xs" />
              {notReady.length} нужно подготовить
            </span>
          </div>
          {devices.length === 0 && (
            <p className="fine">
              Запустите Qorgau на компьютере ученика — он появится здесь
              автоматически.
            </p>
          )}
        </div>
        <div className="before-todo">
          <h3>Что сделать перед тестом</h3>
          {notReady.length ? (
            notReady.slice(0, 4).map((d) => (
              <div className="preparation-row" key={d.id}>
                <strong>{d.name}</strong>
                <span>{preparationIssue(d, "BROWSER")}</span>
              </div>
            ))
          ) : (
            <div className="note ink">
              <CheckCheck size={20} />
              <span>
                {devices.length
                  ? "Все подключённые компьютеры готовы. Можно создать сеанс."
                  : "Скачайте Qorgau и откройте его на компьютерах учеников."}
              </span>
            </div>
          )}
          <button className="btn quiet" onClick={onDevices}>
            Все компьютеры <ChevronRight size={16} />
          </button>
        </div>
      </section>
      <section className="before-guide">
        <h2>Как провести тест</h2>
        <div className="before-steps">
          {[
            [
              "Подготовьте компьютеры",
              "Откройте Qorgau и проверьте камеру на каждом компьютере.",
            ],
            [
              "Создайте сеанс",
              "Выберите сайт или программу, компьютеры и нажмите «Начать тест».",
            ],
            [
              "Принимайте решения",
              "Если появилась пауза, посмотрите запись и решите, продолжать ли тест.",
            ],
          ].map(([title, text], i) => (
            <article className="card" key={title}>
              <StepBubble
                number={i + 1}
                state={
                  i === 0 && devices.length
                    ? "done"
                    : i === 1 && devices.length
                      ? "now"
                      : ""
                }
              />
              <h3>{title}</h3>
              <p>{text}</p>
              {i === 1 && (
                <button className="btn quiet sm" onClick={onNew}>
                  Новый сеанс <ChevronRight size={14} />
                </button>
              )}
            </article>
          ))}
        </div>
      </section>
      <section className="card recent-sessions">
        <div className="card-head">
          <h2>Недавние сеансы</h2>
          <button className="btn quiet sm" onClick={onHistory}>
            Все сеансы и отчёты <ChevronRight size={16} />
          </button>
        </div>
        {recent.length ? (
          recent.map((e) => {
            const own = events.filter((x) => x.exam_id === e.id);
            const pending = pendingEvents(own, Infinity);
            return (
              <div className="recent-row" key={e.id}>
                <div>
                  <span className="fine">
                    {dateLabel(e.created_at)} · {shortClock(e.created_at)}
                  </span>
                  <h3>{e.title}</h3>
                  <p>{Object.keys(e.participants).length} компьютеров</p>
                </div>
                <Pill tone={pending.length ? "warn" : "ok"}>
                  {pending.length
                    ? `${pending.length} событий ждут решения`
                    : own.length
                      ? `${own.length} событий, всё решено`
                      : "Без событий"}
                </Pill>
                <div className="actions">
                  {pending.length > 0 && (
                    <button
                      className="btn primary sm"
                      onClick={() => onReview(pending[0].id)}
                    >
                      Разобрать
                    </button>
                  )}
                  <a className="btn sm" href={`/api/exams/${e.id}/report.csv`}>
                    <Download size={15} />
                    Отчёт
                  </a>
                </div>
              </div>
            );
          })
        ) : (
          <div className="empty">Здесь появятся завершённые сеансы.</div>
        )}
      </section>
    </>
  );
}
function EventsPage({
  events,
  exams,
  onSelect,
}: {
  events: Incident[];
  exams: Exam[];
  onSelect: (id: string) => void;
}) {
  const [filter, setFilter] = useState("ALL"),
    [session, setSession] = useState("ALL"),
    [showAll, setShowAll] = useState(false);
  const selected = events.filter(
      (e) => session === "ALL" || e.exam_id === session,
    ),
    totals = eventTotals(selected);
  const waiting = pendingEvents(selected, Infinity),
    resolved = selected
      .filter(
        (e) =>
          e.decision !== "PENDING" &&
          (filter === "ALL" || filter === e.decision),
      )
      .sort((a, b) => b.created_at - a.created_at);
  return (
    <div className="events-page">
      <div className="events-actions">
        <select
          className="select"
          aria-label="Сеанс событий"
          value={session}
          onChange={(e) => {
            setSession(e.target.value);
            setShowAll(false);
          }}
        >
          <option value="ALL">Все сеансы</option>
          {exams.map((e) => (
            <option key={e.id} value={e.id}>
              {e.title}
            </option>
          ))}
        </select>
        <button
          className="btn primary"
          disabled={!waiting.length}
          onClick={() => onSelect(waiting[0].id)}
        >
          Разобрать по очереди <ChevronRight size={16} />
        </button>
      </div>
      <div className="filters-row">
        <Tabs
          value={filter}
          onChange={(value) => {
            setFilter(value);
            setShowAll(false);
          }}
          ariaLabel="Решение по событию"
          items={[
            { value: "ALL", label: "Все", count: selected.length },
            {
              value: "PENDING",
              label: "Ждут решения",
              count: totals.pending,
              tone: "warn",
            },
            { value: "CONFIRMED", label: "Нарушение", count: totals.confirmed },
            {
              value: "REJECTED",
              label: "Нарушения нет",
              count: totals.rejected,
            },
          ]}
        />
        <p className="fine">
          «Нарушения нет» убирает отметку, но не снимает паузу.
        </p>
      </div>
      {(filter === "ALL" || filter === "PENDING") && (
        <section className="card event-group waiting">
          <div className="card-head">
            <h2>
              Ждут решения <span className="count">{waiting.length}</span>
            </h2>
            {waiting.length > 0 && (
              <span className="fine">
                Самое старое ждёт{" "}
                {eventAge(Math.min(...waiting.map((e) => e.created_at)))}
              </span>
            )}
          </div>
          <EventTable events={waiting} onSelect={onSelect} />
        </section>
      )}
      {filter !== "PENDING" && (
        <section className="card event-group">
          <div className="card-head">
            <h2>
              Решено <span className="count">{resolved.length}</span>
            </h2>
          </div>
          <EventTable
            events={showAll ? resolved : resolved.slice(0, 4)}
            onSelect={onSelect}
          />
          {resolved.length > 4 && (
            <div className="card-foot">
              <span className="fine">
                Показаны последние {showAll ? resolved.length : 4} из{" "}
                {resolved.length}
              </span>
              <button
                className="btn quiet sm"
                onClick={() => setShowAll(!showAll)}
              >
                {showAll ? "Свернуть" : "Показать все"}
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
function HistoryPage({
  exams,
  events,
  onOpen,
  onReview,
}: {
  exams: Exam[];
  events: Incident[];
  onOpen: (id: string) => void;
  onReview: (id: string) => void;
}) {
  const [filter, setFilter] = useState("ALL");
  const sorted = [...exams].sort((a, b) => b.created_at - a.created_at);
  const [expanded, setExpanded] = useState<string[]>([]);
  const latestExamId = sorted[0]?.id;
  const expandedInitially = useRef(false);
  useEffect(() => {
    if (!expandedInitially.current && latestExamId) {
      setExpanded([latestExamId]);
      expandedInitially.current = true;
    }
  }, [latestExamId]);
  const unresolved = (e: Exam) =>
      events.some((x) => x.exam_id === e.id && x.decision === "PENDING"),
    confirmed = (e: Exam) =>
      events.some((x) => x.exam_id === e.id && x.decision === "CONFIRMED");
  return (
    <div className="history-list">
      <div className="filters-row">
        <Tabs
          value={filter}
          onChange={setFilter}
          ariaLabel="Фильтр сеансов"
          items={[
            { value: "ALL", label: "Все", count: exams.length },
            {
              value: "PENDING",
              label: "С нерешёнными",
              count: exams.filter(unresolved).length,
              tone: "warn",
            },
            {
              value: "CONFIRMED",
              label: "С нарушениями",
              count: exams.filter(confirmed).length,
            },
          ]}
        />
        <div className="legend">
          <span>
            <Bubble state="pause" size="xs" />
            Нарушение
          </span>
          <span>
            <Bubble state="ink" size="xs" />
            Нарушения нет
          </span>
          <span>
            <Bubble state="warn" size="xs" />
            Ждёт решения
          </span>
        </div>
      </div>
      {sorted
        .filter(
          (e) =>
            filter === "ALL" ||
            (filter === "PENDING" ? unresolved(e) : confirmed(e)),
        )
        .map((e) => {
          const ev = events.filter((x) => x.exam_id === e.id),
            totals = eventTotals(ev),
            isOpen = expanded.includes(e.id);
          return (
            <article className="card history-card" key={e.id}>
              <div className="history-title">
                <div>
                  <h2>
                    {e.title}{" "}
                    <Pill tone="ok">
                      {e.status === "COMPLETED"
                        ? "Завершён"
                        : e.status === "RUNNING"
                          ? "Тест идёт"
                          : "Ждёт старта"}
                    </Pill>
                  </h2>
                  <p>
                    {dateLabel(e.created_at)} · начало в{" "}
                    {shortClock(e.created_at)} · {e.group} · {e.room}
                  </p>
                </div>
                <div className="actions">
                  <button className="btn sm" onClick={() => onOpen(e.id)}>
                    Открыть сеанс
                  </button>
                  <a className="btn sm" href={`/api/exams/${e.id}/report.csv`}>
                    <Download size={16} />
                    Отчёт CSV
                  </a>
                </div>
              </div>
              <div className="history-metrics">
                <div>
                  <strong>{Object.keys(e.participants).length}</strong>
                  <span>Учеников</span>
                </div>
                <div>
                  <strong>
                    <QuietNumber value={ev.length} />
                  </strong>
                  <span>Событий</span>
                  <MiniBar {...totals} />
                </div>
                <div>
                  <strong className={totals.confirmed ? "danger-text" : ""}>
                    <QuietNumber value={totals.confirmed} />
                  </strong>
                  <span>Нарушений</span>
                </div>
                <div className={totals.pending ? "pending-metric" : ""}>
                  <strong>
                    <QuietNumber value={totals.pending} />
                  </strong>
                  <span>Ждут решения</span>
                  {totals.pending > 0 && (
                    <button
                      className="btn primary sm"
                      onClick={() =>
                        onReview(pendingEvents(ev, Infinity)[0].id)
                      }
                    >
                      Разобрать
                    </button>
                  )}
                </div>
              </div>
              {isOpen && (
                <>
                  <div className="table-box">
                    <table className="table dense">
                      <thead>
                        <tr>
                          <th>Ученик</th>
                          {[
                            "Вниз",
                            "Влево",
                            "Вправо",
                            "Телефон",
                            "Ждут решения",
                            "Паузы",
                          ].map((t) => (
                            <th className="c" key={t}>
                              {t}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {Object.values(e.participants).map((d) => {
                          const own = ev.filter((x) => x.device_id === d.id),
                            valid = own.filter(
                              (x) => x.decision !== "REJECTED",
                            );
                          return (
                            <tr key={d.id}>
                              <td>
                                <strong>{d.student || d.name}</strong>
                                <small>{d.name}</small>
                              </td>
                              {directions.map(([k]) => (
                                <td className="c" key={k}>
                                  <QuietNumber
                                    value={
                                      valid.filter(
                                        (x) => x.type === "GAZE_" + k,
                                      ).length
                                    }
                                  />
                                </td>
                              ))}
                              <td className="c">
                                <QuietNumber
                                  value={
                                    valid.filter(
                                      (x) => x.type === "PHONE_DETECTED",
                                    ).length
                                  }
                                />
                              </td>
                              <td className="c warn-text">
                                <QuietNumber
                                  value={
                                    own.filter((x) => x.decision === "PENDING")
                                      .length
                                  }
                                />
                              </td>
                              <td className="c">
                                <QuietNumber value={d.state.locks} />
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p className="table-note">
                    Учтены все события сеанса, кроме решений «Нарушения нет».
                    Отметки текущего круга показаны в аудитории.
                  </p>
                </>
              )}
              <div className="card-foot">
                <button
                  className="btn quiet sm"
                  onClick={() =>
                    setExpanded(
                      isOpen
                        ? expanded.filter((id) => id !== e.id)
                        : [...expanded, e.id],
                    )
                  }
                >
                  {isOpen ? "Скрыть учеников" : "Показать учеников"}
                  <ChevronDown size={16} />
                </button>
              </div>
            </article>
          );
        })}
      {!exams.length && <Empty text="Здесь появятся ваши сеансы и отчёты" />}
    </div>
  );
}
function Computers({
  devices,
  busy,
  onRevoke,
}: {
  devices: Device[];
  busy: boolean;
  onRevoke: (d: Device) => void;
}) {
  const ready = devices.filter((d) => !preparationIssue(d, "BROWSER")),
    notReady = devices.filter((d) => preparationIssue(d, "BROWSER"));
  return (
    <>
      <section className="card computers-card">
        <div className="readiness-head">
          <h2>
            <strong>{ready.length}</strong> из {devices.length}{" "}
            <span>готовы к тесту</span>
          </h2>
          <div className="legend">
            <span>
              <Tick yes label="Готов" />
              Всё готово
            </span>
            <span>
              <Tick yes={false} label="Нужно действие" />
              Нужно действие ученика
            </span>
          </div>
        </div>
        {devices.length ? (
          <div className="table-box">
            <table className="table dense readiness-table">
              <thead>
                <tr>
                  <th>Компьютер</th>
                  {[
                    "Связь",
                    "Камера",
                    "Взгляд",
                    "Защита окна",
                    "Qorgau Browser",
                  ].map((t) => (
                    <th className="c" key={t}>
                      {t}
                    </th>
                  ))}
                  <th>Что сделать</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {[
                  [false, notReady],
                  [true, ready],
                ].map(([isReady, group]) => {
                  const list = group as Device[];
                  return list.length ? (
                    <DeviceReadinessGroup
                      key={String(isReady)}
                      ready={Boolean(isReady)}
                      devices={list}
                      busy={busy}
                      onRevoke={onRevoke}
                    />
                  ) : null;
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">
            <Monitor size={30} />
            <p>
              Нет подключённых компьютеров. Запустите Qorgau на компьютере
              ученика.
            </p>
          </div>
        )}
      </section>
      <div className="readiness-download note ink">
        <Download size={24} />
        <div>
          <strong>Qorgau для компьютеров учеников</strong>
          <p>
            Установите приложение или откройте EXE. Компьютер появится здесь
            автоматически.
          </p>
          <div className="actions">
            <a className="btn primary sm" href="/api/student/download">
              Скачать Qorgau
            </a>
            <a className="btn sm" href="/api/student/download-offline">
              Qorgau Offline
            </a>
          </div>
          <p className="fine">
            В автономном режиме настройка и управление выполняются на компьютере
            ученика.
          </p>
        </div>
      </div>
      <p className="fine">
        Если приложение закрыто или связь потеряна, компьютер исчезнет из списка
        в течение 6 секунд. История остаётся в отчётах.
      </p>
    </>
  );
}
function DeviceReadinessGroup({
  ready,
  devices,
  busy,
  onRevoke,
}: {
  ready: boolean;
  devices: Device[];
  busy: boolean;
  onRevoke: (d: Device) => void;
}) {
  return (
    <>
      <tr className={`grp ${ready ? "" : "warn"}`}>
        <td colSpan={8}>
          {ready ? "Готовы" : "Не готовы"} · {devices.length}
        </td>
      </tr>
      {devices.map((d) => (
        <tr key={d.id} className={ready ? "" : "needs-action"}>
          <td>
            <strong>{d.name}</strong>
            {typeof d.capabilities.agent_version === "string" && (
              <small>Qorgau {d.capabilities.agent_version}</small>
            )}
          </td>
          <td className="c">
            <Tick yes={d.online} label="Связь" />
          </td>
          <td className="c">
            <Tick
              yes={Boolean(
                d.capabilities.camera && !d.capabilities.camera_fault,
              )}
              label="Камера"
            />
          </td>
          <td className="c">
            <Tick yes={gazeEnabled(d)} label="Взгляд" />
          </td>
          <td className="c">
            <Tick
              yes={Boolean(
                d.capabilities.window_guard && !d.capabilities.guard_fault,
              )}
              label="Защита окна"
            />
          </td>
          <td className="c">
            <Tick
              yes={d.targets.some((t) => t.id === "qorgau-browser")}
              label="Qorgau Browser"
            />
          </td>
          <td className={ready ? "muted" : "warn-text"}>
            {preparationIssue(d, "BROWSER") || "Готов"}
          </td>
          <td>
            <button
              className="icon-btn revoke-access"
              disabled={busy || d.state.lifecycle === "RUNNING"}
              onClick={() => onRevoke(d)}
              aria-label={`Отозвать доступ ${d.name}`}
            >
              <Trash2 size={17} />
            </button>
          </td>
        </tr>
      ))}
    </>
  );
}
function DeviceTimeline({
  device,
  exam,
  events,
}: {
  device: Device;
  exam?: Exam;
  events: Incident[];
}) {
  if (!exam) return null;
  const now = Date.now() / 1000,
    end =
      exam.status === "COMPLETED"
        ? Math.max(exam.created_at, ...events.map((e) => e.created_at))
        : now;
  const span = Math.max(1, end - exam.created_at),
    position = (at: number) =>
      `${Math.max(0, Math.min(100, ((at - exam.created_at) / span) * 100))}%`;
  const pause =
    device.state.access === "LOCKED"
      ? [...events]
          .sort((a, b) => b.created_at - a.created_at)
          .find((e) => e.type === device.state.reason)
      : undefined;
  return (
    <section className="device-timeline">
      <div className="card-head">
        <h3>Ход теста</h3>
        <span className="fine">
          {shortClock(exam.created_at)} —{" "}
          {exam.status === "COMPLETED" ? "последнее событие" : "сейчас"}
        </span>
      </div>
      {pause && (
        <div className="note pause">
          <Pause size={18} />
          <span>
            Тест на паузе с {shortClock(pause.created_at)}:{" "}
            {eventNames[pause.type] || pause.type}
          </span>
        </div>
      )}
      <div className="test-progress" aria-label="События от начала сеанса">
        <span className="progress-running" />
        {pause && (
          <span
            className="progress-paused"
            style={{ left: position(pause.created_at) }}
          />
        )}
        {events.map((e) => (
          <button
            key={e.id}
            type="button"
            className={`progress-event ${e.id === pause?.id ? "paused" : ""}`}
            style={{ left: position(e.created_at) }}
            aria-label={`${shortClock(e.created_at)}: ${eventNames[e.type] || e.type}`}
            title={`${shortClock(e.created_at)}: ${eventNames[e.type] || e.type}`}
          />
        ))}
      </div>
      <div className="progress-events">
        {[...events]
          .sort((a, b) => a.created_at - b.created_at)
          .map((e) => (
            <span key={e.id}>
              <Bubble size="xs" state={e.id === pause?.id ? "pause" : "ink"} />
              <span>
                <strong>{shortClock(e.created_at)}</strong>
                <small>{eventNames[e.type] || e.type}</small>
              </span>
            </span>
          ))}
      </div>
    </section>
  );
}

function Counters({ d }: { d: Device }) {
  if (
    !d.simulated &&
    !gazeEnabled(d) &&
    !Object.values(d.state.counts).some(Boolean)
  ) {
    return (
      <div className="notice">
        Контроль взгляда пока недоступен. Включите камеру в актуальной версии
        Qorgau на этом компьютере.
      </div>
    );
  }
  return (
    <div className="counters">
      {directions.map(([id, label]) => (
        <div key={id}>
          <span>{label}</span>
          <strong className={d.state.counts[id] >= 3 ? "danger-text" : ""}>
            {d.state.counts[id]}
            <small>/3</small>
          </strong>
          <div className="ticks">
            {[1, 2, 3].map((n) => (
              <i
                key={n}
                className={
                  d.state.counts[id] >= n ? (n === 3 ? "red" : "filled") : ""
                }
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
function Empty({ text }: { text: string }) {
  return (
    <div className="empty panel">
      <ClipboardCheck size={30} />
      <h3>{text}</h3>
    </div>
  );
}
function Rule({
  number,
  title,
  text,
}: {
  number: number;
  title: string;
  text: string;
}) {
  const flows: Record<
    number,
    [string, string, "pause" | "warn" | "ok" | ""][]
  > = {
    1: [
      ["5 секунд в одну сторону", "+1 отметка", ""],
      ["3 отметки в одну сторону", "Тест на паузе", "pause"],
    ],
    2: [["2 уверенных обнаружения подряд", "Тест на паузе", "pause"]],
    3: [
      ["Лица нет 3 секунды", "Ждёт решения", "warn"],
      ["Лица нет 10 секунд", "Техническая пауза", "pause"],
    ],
    4: [["Контроль недоступен", "Тест на паузе", "pause"]],
    5: [["Второе лицо от 1 секунды", "Ждёт решения", "warn"]],
    6: [["3 взгляда за минуту, всего от 6 с", "Ждёт решения", "warn"]],
    7: [["Подъём и удержание телефона", "Ждёт решения", "warn"]],
    8: [["«Продолжить тест»", "Новый круг отметок", "ok"]],
    9: [["Конец контроля", "История сохранена", "ok"]],
  };
  return (
    <article className="rule">
      <div className="rule-text">
        <StepBubble number={number} />
        <div>
          <h3>{title}</h3>
          <p>{text}</p>
        </div>
      </div>
      <div className="rule-flow">
        {flows[number]?.map(([condition, outcome, tone]) => (
          <div className="flow" key={condition}>
            <span className="cond">{condition}</span>
            <span className="lead" />
            <span className="out">
              <Pill tone={tone}>{outcome}</Pill>
            </span>
          </div>
        ))}
      </div>
    </article>
  );
}
function EventTable({
  events,
  onSelect,
}: {
  events: Incident[];
  onSelect: (id: string) => void;
}) {
  return events.length === 0 ? (
    <div className="empty">
      <CheckCheck size={28} />
      <p>Событий пока нет</p>
    </div>
  ) : (
    <div className="table-box">
      <table className="table">
        <thead>
          <tr>
            <th>Ученик</th>
            <th>Событие</th>
            <th>Запись</th>
            <th>
              {events[0].decision === "PENDING" ? "Статус" : "Ваше решение"}
            </th>
            <th />
          </tr>
        </thead>
        <tbody>
          {events.map((e, i) => {
            const review = e.reviews.at(-1);
            return (
              <tr key={e.id}>
                <td>
                  <strong>{e.student || e.device_name}</strong>
                  <small>{e.device_name}</small>
                </td>
                <td>
                  <strong>{eventNames[e.type] || e.type}</strong>
                  <small>
                    {dateLabel(e.created_at)} · {clock(e.created_at)}
                  </small>
                </td>
                <td>
                  {e.media.length ? (
                    <div className="event-record">
                      <EventThumb event={e} compact />
                      <span>
                        {e.media.some(
                          (m) =>
                            m.clip_end !== undefined &&
                            m.clip_start !== undefined,
                        )
                          ? `${e.media.reduce((sum, m) => sum + Math.max(0, (m.clip_end ?? 0) - (m.clip_start ?? 0)), 0).toFixed(0)} с`
                          : `${e.media.length} фрагм.`}
                      </span>
                    </div>
                  ) : (
                    <span className="muted">Без записи</span>
                  )}
                </td>
                <td>
                  <Pill
                    tone={
                      e.decision === "PENDING"
                        ? "warn"
                        : e.decision === "CONFIRMED"
                          ? "pause"
                          : "ok"
                    }
                  >
                    {e.decision === "PENDING"
                      ? `Ждёт · ${eventAge(e.created_at)}`
                      : decisionNames[e.decision]}
                  </Pill>
                  {review && (
                    <small>
                      {review.author}, в {shortClock(review.at)}
                    </small>
                  )}
                </td>
                <td>
                  <button
                    className={`btn sm ${e.decision === "PENDING" && i === 0 ? "primary" : "quiet"}`}
                    onClick={() => onSelect(e.id)}
                  >
                    {e.decision === "PENDING" ? "Разобрать" : "Открыть"}
                    <ChevronRight size={15} />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
function NewExam({
  devices,
  onClose,
  onCreate,
  onRules,
}: {
  devices: Device[];
  onClose: () => void;
  onCreate: (body: unknown) => Promise<void>;
  onRules: () => void;
}) {
  const available = devices.filter(
    (d) =>
      (!d.exam_id || d.state.lifecycle === "COMPLETED") &&
      !d.capabilities.recording_tail,
  );
  const [environment, setEnvironment] = useState<TestEnvironment>("BROWSER");
  const [testUrl, setTestUrl] = useState("");
  const ready = (d: Device) => preparationIssue(d, environment) === null;
  const [selected, setSelected] = useState<string[]>(
    available.filter(ready).map((d) => d.id),
  );
  const selectionInitialized = useRef(available.length > 0);
  useEffect(() => {
    if (!selectionInitialized.current && available.length > 0) {
      setSelected(available.filter(ready).map((d) => d.id));
      selectionInitialized.current = true;
    }
  }, [devices, environment]);
  const [studentNames, setStudentNames] = useState<Record<string, string>>({});
  const [title, setTitle] = useState("Контроль аудитории");
  const [showAll, setShowAll] = useState(false);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const chosen = available.filter((d) => selected.includes(d.id) && ready(d));
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!chosen.length) return;
    setBusy(true);
    try {
      await onCreate({
        title,
        group: "Аудитория",
        room: "Подключённые компьютеры",
        device_ids: chosen.map((d) => d.id),
        student_names: Object.fromEntries(
          chosen.map((d) => [d.id, studentNames[d.id]?.trim() || ""]),
        ),
        mode: "GUARDED",
        require_camera: true,
        environment:
          environment === "BROWSER"
            ? {
                kind: "BROWSER",
                target_id: "qorgau-browser",
                url: testUrl.trim(),
              }
            : { kind: "DESKTOP" },
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const prepared = available.filter(ready),
    unprepared = available.filter((d) => !ready(d));
  return (
    <Modal
      title="Новый сеанс"
      subtitle="Где пройдёт тест и на каких компьютерах"
      onClose={onClose}
      className="new-exam-modal"
      wide
    >
      <form onSubmit={submit}>
        <div className="new-exam-grid">
          <div className="new-exam-settings">
            <label className="field">
              Название
              <input
                className="input"
                autoFocus
                required
                minLength={2}
                maxLength={120}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <fieldset className="environment-choice">
              <legend>Где проходит тест</legend>
              <div className="environment-options">
                {(
                  [
                    [
                      "BROWSER",
                      "Сайт в Qorgau Browser",
                      "Одно окно без вкладок. Переходы в пределах сайта.",
                    ],
                    [
                      "WINDOW",
                      "Программа на компьютере",
                      "Окно, которое ученик выбрал в Qorgau заранее.",
                    ],
                  ] as const
                ).map(([kind, label, description]) => (
                  <label
                    className={environment === kind ? "selected" : ""}
                    key={kind}
                  >
                    <input
                      type="radio"
                      name="test-environment"
                      value={kind}
                      checked={environment === kind}
                      onChange={() => {
                        setEnvironment(kind);
                        setSelected(
                          available
                            .filter((d) => preparationIssue(d, kind) === null)
                            .map((d) => d.id),
                        );
                      }}
                    />
                    <span className="environment-icon">
                      {kind === "BROWSER" ? (
                        <Globe size={22} />
                      ) : (
                        <AppWindow size={22} />
                      )}
                    </span>
                    <span>
                      <strong>{label}</strong>
                      <small>{description}</small>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
            {environment === "BROWSER" && (
              <label className="field">
                Адрес теста
                <input
                  className="input"
                  type="url"
                  required
                  pattern="https?://.+"
                  value={testUrl}
                  onChange={(e) => setTestUrl(e.target.value)}
                  placeholder="https://example.kz/test"
                  autoComplete="url"
                />
                <span className="field-help">
                  Прямая ссылка на тест. Другие сайты и новые окна открыть не
                  получится.
                </span>
              </label>
            )}
            <section className="card rules-preview">
              <div className="card-head">
                <h3>Правила контроля</h3>
                <button
                  className="btn quiet sm"
                  type="button"
                  onClick={onRules}
                >
                  Посмотреть все
                </button>
              </div>
              <div>
                <span>Три отметки за взгляд в одну сторону</span>
                <Pill tone="pause">пауза</Pill>
              </div>
              <div>
                <span>Телефон в кадре два раза подряд</span>
                <Pill tone="pause">пауза</Pill>
              </div>
              <div>
                <span>Второе лицо, подъём телефона и другое</span>
                <Pill tone="warn">проверка</Pill>
              </div>
            </section>
            <div className="note ink">
              <Pause size={20} />
              <span>
                Если тест встанет на паузу, продолжить его можете только вы.
                Ученик зовёт вас кнопкой на экране или клавишами{" "}
                <strong>Ctrl+Alt+Q</strong>.
              </span>
            </div>
            {chosen.some((d) => !gazeEnabled(d)) && (
              <div className="note warn">
                У части компьютеров контроль взгляда выключен. Подготовьте его
                перед началом теста.
              </div>
            )}
          </div>
          <section className="new-exam-devices">
            <div className="new-exam-device-head">
              <div>
                <h3>Компьютеры</h3>
                <p className="fine">
                  Выбрано{" "}
                  <strong>
                    {chosen.length} из {available.length}
                  </strong>
                  . Имена можно не вводить.
                </p>
              </div>
              <button
                className="btn sm"
                type="button"
                onClick={() => setSelected(prepared.map((d) => d.id))}
              >
                Выбрать все готовые
              </button>
            </div>
            {!available.length && (
              <div className="note">
                Нет свободных компьютеров онлайн. Запустите Qorgau или завершите
                предыдущий сеанс.
              </div>
            )}
            {prepared.length > 0 && (
              <div className="card assignment-group">
                <header>
                  <span>
                    <Bubble state="on" size="xs" />
                    Готовы, {prepared.length}
                  </span>
                  <span>Ученик</span>
                </header>
                {(showAll ? prepared : prepared.slice(0, 6)).map((d) => (
                  <div className="workstation-assignment" key={d.id}>
                    <label className="workstation-choice">
                      <input
                        type="checkbox"
                        checked={selected.includes(d.id)}
                        onChange={(e) =>
                          setSelected(
                            e.target.checked
                              ? [...selected, d.id]
                              : selected.filter((id) => id !== d.id),
                          )
                        }
                      />
                      <strong>{d.name}</strong>
                    </label>
                    <input
                      className="input"
                      aria-label={`Ученик на ${d.name}`}
                      placeholder="Имя, если нужно"
                      maxLength={80}
                      value={studentNames[d.id] || ""}
                      onChange={(e) =>
                        setStudentNames({
                          ...studentNames,
                          [d.id]: e.target.value,
                        })
                      }
                    />
                  </div>
                ))}
                {prepared.length > 6 && (
                  <button
                    type="button"
                    className="btn quiet sm block"
                    onClick={() => setShowAll(!showAll)}
                  >
                    {showAll
                      ? "Скрыть"
                      : "Ещё " + (prepared.length - 6) + " компьютеров"}
                    <ChevronDown size={16} />
                  </button>
                )}
              </div>
            )}
            {unprepared.length > 0 && (
              <div className="card assignment-group unprepared">
                <header>
                  <span>
                    <Bubble state="warn" size="xs" />
                    Не готовы, {unprepared.length}. Сначала исправьте
                  </span>
                </header>
                {unprepared.map((d) => (
                  <div className="workstation-assignment" key={d.id}>
                    <label className="workstation-choice">
                      <input type="checkbox" disabled />
                      <strong>{d.name}</strong>
                    </label>
                    <span className="warn-text">
                      {preparationIssue(d, environment)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
        {error && (
          <div className="error new-exam-error" role="alert">
            {error}
          </div>
        )}
        <footer className="modal-foot">
          <p className="fine">
            Тест не начнётся сразу: сначала сеанс появится в аудитории.
          </p>
          <div className="actions">
            <button className="btn" type="button" onClick={onClose}>
              Отмена
            </button>
            <button className="btn primary" disabled={busy || !chosen.length}>
              {busy && <LoaderCircle className="spin" size={17} />}Создать сеанс
              на {chosen.length} компьютерах
            </button>
          </div>
        </footer>
      </form>
    </Modal>
  );
}

function EvidenceTimeline({
  event,
  mediaIndex,
}: {
  event: Incident;
  mediaIndex: number;
}) {
  const media = event.media[mediaIndex];
  if (!media) return null;
  const clipStart = media.clip_start ?? Math.max(0, event.start - 5);
  const clipEnd = media.clip_end ?? Math.max(event.at + 5, clipStart + 1);
  const span = Math.max(0.1, clipEnd - clipStart);
  const position = (value: number) =>
    `${Math.max(0, Math.min(100, ((value - clipStart) / span) * 100))}%`;
  const absoluteStart = event.created_at - (event.at - clipStart);
  return (
    <div className="evidence-timeline" aria-label="Временная шкала записи">
      <div className="timeline-labels">
        <span>{clock(absoluteStart)}</span>
        <strong>Событие {clock(absoluteStart + event.at - clipStart)}</strong>
        <span>{clock(absoluteStart + span)}</span>
      </div>
      <div className="timeline-track">
        {(media.gaps || []).map(([start, end], gap) => (
          <i
            key={gap}
            className="timeline-gap"
            style={{
              left: position(start),
              width: `${Math.max(1, ((end - start) / span) * 100)}%`,
            }}
          />
        ))}
        <i className="timeline-event" style={{ left: position(event.at) }} />
        <i className="timeline-start" style={{ left: position(event.start) }} />
      </div>
      <div className="timeline-legend">
        <span>
          <Bubble state="ink" size="xs" />
          начало эпизода
        </span>
        <span>
          <Bubble state="pause" size="xs" />
          срабатывание
        </span>
        {media.complete === false && (
          <span>
            <Bubble state="off" size="xs" />
            нет записи
          </span>
        )}
      </div>
    </div>
  );
}
function EventReview({
  event: e,
  queue,
  onSelect,
  onClose,
  onUpdate,
  notify,
}: {
  event: Incident;
  queue: Incident[];
  onSelect: (id: string) => void;
  onClose: () => void;
  onUpdate: () => Promise<void>;
  notify: (s: string) => void;
}) {
  const [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [index, setIndex] = useState(0);
  const expiresAt = e.media[index]?.expires_at;
  const queueIndex = queue.findIndex((event) => event.id === e.id);
  const successor = useRef(queue[queueIndex + 1]?.id);
  const next =
    queueIndex >= 0
      ? queue[queueIndex + 1] || queue.find((event) => event.id !== e.id)
      : queue.find((event) => event.id === successor.current) || queue[0];
  const previous = queueIndex > 0 ? queue[queueIndex - 1] : undefined;
  async function decide(decision: string) {
    successor.current = next?.id;
    setBusy(true);
    try {
      await api("/events/" + e.id + "/review", {
        decision,
        expected_revision: e.revision,
        reason,
      });
      await onUpdate();
      notify("Решение сохранено");
      setReason("");
    } catch (err) {
      notify((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={eventNames[e.type] || e.type}
      subtitle={`${e.student || e.device_name}, ${e.device_name}, ${dateLabel(e.created_at)} в ${clock(e.created_at)}`}
      onClose={onClose}
      wide
      className="event-review-modal"
      headerExtra={
        <div className="event-navigator">
          <button
            className="icon-btn"
            disabled={!previous || busy}
            aria-label="Предыдущее событие"
            onClick={() => previous && onSelect(previous.id)}
          >
            <ChevronLeft size={18} />
          </button>
          <span>
            {queueIndex >= 0
              ? `Событие ${queueIndex + 1} из ${queue.length}`
              : "Событие разобрано"}
          </span>
          <button
            className="icon-btn"
            disabled={!next || busy}
            aria-label="Следующее событие"
            onClick={() => next && onSelect(next.id)}
          >
            <ChevronRight size={18} />
          </button>
        </div>
      }
    >
      <div className="event-review-grid">
        <section className="event-review-main">
          <div className="student-status">
            <Pill
              tone={
                e.decision === "PENDING"
                  ? "warn"
                  : e.decision === "CONFIRMED"
                    ? "pause"
                    : "ok"
              }
            >
              {decisionNames[e.decision]}
            </Pill>
            <span className="fine">Версия решения {e.revision}</span>
          </div>
          {e.media.length ? (
            <>
              <div className="cam evidence-camera">
                <RegMarks light />
                {e.media[index]?.url ? (
                  e.media[index].mime.startsWith("image/") ? (
                    <img
                      className="evidence-image"
                      src={e.media[index].url}
                      alt={`Кадр события: ${eventNames[e.type] || e.type}`}
                    />
                  ) : (
                    <video
                      className="evidence-video"
                      aria-label={`Запись события: ${eventNames[e.type] || e.type}`}
                      key={e.media[index]?.id}
                      controls
                      preload="metadata"
                      onLoadedMetadata={(x) => {
                        const offset = e.at - (e.media[index]?.clip_start || 0);
                        if (offset > 0 && offset < x.currentTarget.duration)
                          x.currentTarget.currentTime = offset;
                      }}
                      src={e.media[index]?.url}
                    />
                  )
                ) : (
                  <div className="video-empty">
                    <Video size={38} />
                    <p>Предпросмотр записи недоступен</p>
                  </div>
                )}
              </div>
              {e.media.length > 1 && (
                <select
                  className="select"
                  aria-label="Фрагмент видео"
                  value={index}
                  onChange={(x) => setIndex(Number(x.target.value))}
                >
                  {e.media.map((m, i) => (
                    <option key={m.id} value={i}>
                      Фрагмент {i + 1}
                    </option>
                  ))}
                </select>
              )}
              <EvidenceTimeline event={e} mediaIndex={index} />
              {e.media[index]?.complete === false && (
                <div className="note warn" role="status">
                  Запись неполная: отсутствует часть нужного интервала. Разрывы:{" "}
                  {e.media[index]?.gaps
                    ?.map(([a, b]) => `${a.toFixed(1)}–${b.toFixed(1)} с`)
                    .join(", ") || "начало или конец фрагмента"}
                  .
                </div>
              )}
            </>
          ) : (
            <div className="video-empty">
              <Video size={34} />
              <h3>Запись не прикреплена</h3>
              <p>
                {e.media_expired_at
                  ? "Срок хранения записи истёк. Событие и решения сохранены."
                  : "Компьютер ещё не передал фрагмент. Решение доступно, но визуального подтверждения пока нет."}
              </p>
            </div>
          )}
          {e.type === "PHONE_AIM_REVIEW" && (
            <div className="note ink evidence-context">
              <Smartphone size={18} />
              <span>
                Система отметила подъём и удержание телефона. Направление
                объектива и факт снимка не установлены — проверьте запись.
              </span>
            </div>
          )}
          <section className="review-log">
            <h3>История события</h3>
            <div>
              <Bubble state="ink" size="xs" />
              <span>
                <strong>Qorgau отметил событие</strong>
                <small>{clock(e.created_at)}</small>
              </span>
            </div>
            {e.reviews.map((review, i) => (
              <div key={i}>
                <Bubble
                  state={review.decision === "CONFIRMED" ? "pause" : "ink"}
                  size="xs"
                />
                <span>
                  <strong>
                    {
                      decisionNames[
                        review.decision as keyof typeof decisionNames
                      ]
                    }
                  </strong>
                  <small>
                    {review.author}, {clock(review.at)}
                  </small>
                  <p>{review.reason}</p>
                </span>
              </div>
            ))}
          </section>
        </section>
        <aside className="event-review-aside">
          <dl className="event-details">
            <div>
              <dt>Начало эпизода</dt>
              <dd>{clock(e.created_at - (e.at - e.start))}</dd>
            </div>
            <div>
              <dt>Срабатывание</dt>
              <dd>{clock(e.created_at)}</dd>
            </div>
            {e.duration !== undefined && (
              <div>
                <dt>Длительность</dt>
                <dd>{e.duration.toFixed(1)} с</dd>
              </div>
            )}
            {expiresAt && (
              <div>
                <dt>Хранится до</dt>
                <dd>
                  {dateLabel(expiresAt)}, {shortClock(expiresAt)}
                </dd>
              </div>
            )}
          </dl>
          <p className="fine">
            Видео удаляется через 2 часа после загрузки. Журнал событий и
            решения сохраняются.
          </p>
          <p className="fine">
            Событие — повод посмотреть запись. Решение о нарушении принимаете
            вы.
          </p>
          <label className="field">
            Комментарий к решению
            <textarea
              className="input"
              value={reason}
              maxLength={500}
              onChange={(x) => setReason(x.target.value)}
              placeholder="Что видно на записи и почему вы приняли это решение"
            />
          </label>
          <div className="decision-actions">
            <button
              className="btn block"
              disabled={busy || !reason.trim()}
              onClick={() => decide("REJECTED")}
            >
              <Check size={18} />
              Нарушения нет
            </button>
            <button
              className="btn dark block"
              disabled={busy || !reason.trim()}
              onClick={() => decide("CONFIRMED")}
            >
              <ShieldCheck size={18} />
              Это нарушение
            </button>
          </div>
          <p className="fine">
            Решение «Нарушения нет» убирает отметку. Пауза снимается отдельно
            кнопкой «Продолжить тест».
          </p>
          {next && (
            <button
              className="next-event"
              disabled={busy}
              onClick={() => onSelect(next.id)}
            >
              <span>
                <small>Дальше в очереди</small>
                <strong>{eventNames[next.type] || next.type}</strong>
                <span>{next.student || next.device_name}</span>
              </span>
              <ChevronRight size={20} />
            </button>
          )}
        </aside>
      </div>
    </Modal>
  );
}
