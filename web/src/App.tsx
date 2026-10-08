import Classroom from "./Classroom";
import TeacherFaces from "./TeacherFaces";
import ControlStatus from "./ControlStatus";
import { preparationIssue, type TestEnvironment } from "./sessionStatus";
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
  LayoutDashboard,
  Monitor,
  ClipboardCheck,
  History,
  Settings2,
  Plus,
  ArrowUpRight,
  ChevronRight,
  X,
  Play,
  LockKeyhole,
  UnlockKeyhole,
  Check,
  Wifi,
  WifiOff,
  Eye,
  Smartphone,
  Users,
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
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
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
        className={"modal " + (wide ? "wide" : "")}
      >
        <header>
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
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
    [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
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
    <div className="auth-layout">
      <aside className="auth-story">
        <div className="brand">
          <ShieldCheck />
          <span>
            qorgau<span className="brand-dot">.</span>
          </span>
        </div>
        <div>
          <span className="eyebrow">ЛОКАЛЬНЫЙ ПРОКТОРИНГ</span>
          <h1>
            Тест проходит
            <br />
            привычно.
            <br />
            <em>Контроль — рядом.</em>
          </h1>
          <p>
            Единая панель преподавателя для наблюдения за аудиторией и разбора
            спорных моментов.
          </p>
          <div className="auth-points">
            <span>
              <Monitor size={18} /> Любая согласованная среда теста
            </span>
            <span>
              <Eye size={18} /> События с контекстом и видео
            </span>
            <span>
              <ShieldCheck size={18} /> Решение остаётся за преподавателем
            </span>
          </div>
        </div>
        <footer>Qostanai Industry Hackathon · 2026</footer>
      </aside>
      <main className="auth-form">
        <div>
          <span className="eyebrow">ПАНЕЛЬ ПРЕПОДАВАТЕЛЯ</span>
          <h2>{setup ? "Начнём с вашего кабинета" : "С возвращением"}</h2>
          <p>
            {setup
              ? "Создайте локальную учётную запись. Она будет управлять сеансами этой установки."
              : "Войдите, чтобы продолжить работу с аудиторией."}
          </p>
          <form onSubmit={submit}>
            <label>
              Имя преподавателя
              <input
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
            <label>
              Пароль
              <input
                required
                type="password"
                minLength={8}
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={setup ? "new-password" : "current-password"}
                placeholder="Не менее 8 символов"
              />
            </label>
            {error && <div className="error">{error}</div>}
            <button className="btn primary full" disabled={busy}>
              {busy ? (
                <LoaderCircle className="spin" size={18} />
              ) : (
                <ArrowUpRight size={18} />
              )}{" "}
              {setup ? "Создать кабинет" : "Войти в кабинет"}
            </button>
          </form>
          <p className="fine">
            Данные этой установки хранятся на локальном сервере. Камеры
            включаются только при запуске агента с режимом видео.
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
    [page, setPage] = useState("devices"),
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
  const exam = data.exams.find((x) => x.id === examId) || data.exams[0];
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
    if (sessionActive && !["room", "blocked", "teachers"].includes(page))
      setPage("room");
    if (!sessionActive && page === "blocked") setPage("room");
  }, [sessionActive, page]);
  const events = exam ? data.events.filter((e) => e.exam_id === exam.id) : [];
  const selectedDevice =
    devices.find((d) => d.id === deviceId) ||
    data.devices.find((d) => d.id === deviceId);
  const selectedEvent = data.events.find((e) => e.id === eventId);
  const pending = data.events.filter((e) => e.decision === "PENDING");
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
        { id: "room", name: "Текущие компьютеры", icon: Monitor },
        { id: "blocked", name: "Заблокированные", icon: LockKeyhole },
        { id: "teachers", name: "Преподаватели", icon: Users },
      ]
    : [
        { id: "room", name: "Аудитория", icon: LayoutDashboard },
        { id: "review", name: "Проверка событий", icon: ClipboardCheck },
        { id: "history", name: "Сеансы и отчёты", icon: History },
        { id: "devices", name: "Компьютеры", icon: Monitor },
        { id: "rules", name: "Правила контроля", icon: Settings2 },
        { id: "teachers", name: "Преподаватели", icon: Users },
      ];
  return (
    <div className="app">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("room");
          }}
        >
          <ShieldCheck />
          <span>
            qorgau<span className="brand-dot">.</span>
          </span>
        </a>
        <span className="nav-label">ПРЕПОДАВАТЕЛЬ</span>
        <nav>
          {nav.map((n) => (
            <button
              key={n.id}
              aria-label={n.name}
              title={n.name}
              onClick={() => setPage(n.id)}
              className={page === n.id ? "active" : ""}
            >
              <n.icon size={19} />
              <span>{n.name}</span>
              {n.id === "blocked" &&
                devices.some((d) => d.state.access === "LOCKED") && (
                  <b>
                    {devices.filter((d) => d.state.access === "LOCKED").length}
                  </b>
                )}
              {n.id === "review" && pending.length > 0 && (
                <b>{pending.length}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-tip">
          <ShieldCheck size={24} />
          <strong>Вы принимаете решение</strong>
          <p>
            Система отмечает события. Спорные моменты проверяет преподаватель.
          </p>
        </div>
        <div className="profile">
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
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            Кабинет преподавателя <ChevronRight size={14} />
            <strong>{nav.find((n) => n.id === page)?.name}</strong>
          </div>
          <div className="connection">
            {connected ? <Wifi size={15} /> : <WifiOff size={15} />}
            <span>
              {connected ? "Сервер подключён" : "Восстанавливаем связь"}
            </span>
            <span className={"dot " + (connected ? "green" : "amber")} />
          </div>
        </header>
        <main className="main">
          <div className="page-heading">
            <div>
              <div className="eyebrow">QORGAU / КАБИНЕТ ПРЕПОДАВАТЕЛЯ</div>
              <h1>
                {page === "blocked"
                  ? "Заблокированные"
                  : page === "room"
                    ? sessionActive
                      ? "Текущие компьютеры"
                      : "Аудитория"
                    : page === "review"
                      ? "Проверка событий"
                      : page === "history"
                        ? "Сеансы и отчёты"
                        : page === "devices"
                          ? "Компьютеры аудитории"
                          : page === "teachers"
                            ? "Преподаватели"
                            : "Правила контроля"}
              </h1>
              <p>
                {["room", "blocked"].includes(page)
                  ? exam
                    ? `${exam.title} · ${exam.group}`
                    : "Подключите компьютеры и начните первый сеанс."
                  : page === "review"
                    ? "Изучите контекст и подтвердите или отклоните событие."
                    : page === "history"
                      ? "Результаты контроля, решения и записи каждого сеанса."
                      : page === "devices"
                        ? "Здесь только компьютеры с работающим приложением Qorgau."
                        : page === "teachers"
                          ? "Лица преподавателей для подтверждения на рабочем месте."
                          : "Отдельные счётчики направлений. Контекст вместо автоматических обвинений."}
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
            {page === "devices" && (
              <a className="btn primary" href="/api/student/download">
                <Download size={18} /> Скачать приложение
              </a>
            )}
          </div>
          {["room", "blocked"].includes(page) && (
            <>
              {!exam ? (
                <section className="welcome panel">
                  <div className="welcome-icon">
                    <Monitor size={35} />
                  </div>
                  <Badge tone="green">
                    Подключено компьютеров: {liveDevices.length}
                  </Badge>
                  <h2>Компьютеры появляются автоматически</h2>
                  <p>
                    Установите и запустите Qorgau на компьютере ученика. Он
                    появится в кабинете после подключения к серверу. Включите
                    камеру и выберите компьютер для контроля.
                  </p>
                  <div className="button-row">
                    <button
                      className="btn primary"
                      disabled={!liveDevices.length}
                      onClick={() => setNewExam(true)}
                    >
                      <Play size={18} /> Новый сеанс
                    </button>
                    <button className="btn" onClick={() => setPage("devices")}>
                      Компьютеры онлайн · {liveDevices.length}{" "}
                      <ArrowUpRight size={16} />
                    </button>
                    <a className="btn" href="/api/student/download">
                      <Download size={16} /> Скачать Qorgau
                    </a>
                  </div>
                  <div className="welcome-steps">
                    <div>
                      <span>01</span>
                      <strong>Выберите компьютеры</strong>
                      <p>Только подключённые агенты</p>
                    </div>
                    <div>
                      <span>02</span>
                      <strong>Начните контроль</strong>
                      <p>Тест остаётся в привычной системе</p>
                    </div>
                    <div>
                      <span>03</span>
                      <strong>Проверьте события</strong>
                      <p>Решения сохраняются в отчёте</p>
                    </div>
                  </div>
                </section>
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
            <section className="panel">
              <div className="panel-toolbar">
                <div>
                  <h2>
                    Очередь проверки{" "}
                    <span className="count">{pending.length}</span>
                  </h2>
                  <p>Отклонение события само по себе не снимает блокировку.</p>
                </div>
              </div>
              <EventTable events={data.events} onSelect={setEventId} />
            </section>
          )}
          {page === "history" && (
            <div className="history-list">
              {data.exams.length === 0 ? (
                <Empty text="Здесь появятся ваши сеансы и отчёты" />
              ) : (
                data.exams.map((e) => {
                  const ev = data.events.filter((x) => x.exam_id === e.id);
                  return (
                    <article className="panel history-card" key={e.id}>
                      <div className="history-title">
                        <span className="session-icon">
                          <ClipboardCheck size={23} />
                        </span>
                        <div>
                          <h2>{e.title}</h2>
                          <p>
                            {new Date(e.created_at * 1000).toLocaleDateString(
                              "ru-RU",
                            )}{" "}
                            · {e.group} · {e.room}
                          </p>
                        </div>
                        <Badge
                          tone={e.status === "COMPLETED" ? "neutral" : "green"}
                        >
                          {e.status === "COMPLETED"
                            ? "Завершён"
                            : e.status === "RUNNING"
                              ? "Идёт контроль"
                              : "Готов к запуску"}
                        </Badge>
                      </div>
                      <div className="history-metrics">
                        <div>
                          <strong>{Object.keys(e.participants).length}</strong>
                          <span>учеников</span>
                        </div>
                        <div>
                          <strong>{ev.length}</strong>
                          <span>событий</span>
                        </div>
                        <div>
                          <strong>
                            {
                              ev.filter((x) => x.decision === "CONFIRMED")
                                .length
                            }
                          </strong>
                          <span>подтверждено</span>
                        </div>
                        <div>
                          <strong>
                            {ev.filter((x) => x.decision === "PENDING").length}
                          </strong>
                          <span>на проверке</span>
                        </div>
                        <button
                          className="btn"
                          onClick={() => {
                            setExamId(e.id);
                            setPage("room");
                          }}
                        >
                          Открыть сеанс <ChevronRight size={16} />
                        </button>
                        <a
                          className="btn"
                          href={"/api/exams/" + e.id + "/report.csv"}
                        >
                          <Download size={16} /> Отчёт CSV
                        </a>
                      </div>
                      <div className="table-scroll">
                        <table>
                          <thead>
                            <tr>
                              <th>Ученик</th>
                              <th>Вниз</th>
                              <th>Влево</th>
                              <th>Вправо</th>
                              <th>Телефон</th>
                              <th>На проверке</th>
                              <th>Блокировки</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.values(e.participants).map((d) => {
                              const own = ev.filter(
                                  (x) => x.device_id === d.id,
                                ),
                                valid = own.filter(
                                  (x) => x.decision !== "REJECTED",
                                );
                              return (
                                <tr key={d.id}>
                                  <td>
                                    <strong>{d.student}</strong>
                                    <small>{d.name}</small>
                                  </td>
                                  {directions.map(([k]) => (
                                    <td key={k}>
                                      {
                                        valid.filter(
                                          (x) => x.type === "GAZE_" + k,
                                        ).length
                                      }
                                    </td>
                                  ))}
                                  <td>
                                    {
                                      valid.filter(
                                        (x) => x.type === "PHONE_DETECTED",
                                      ).length
                                    }
                                  </td>
                                  <td>
                                    {
                                      own.filter(
                                        (x) => x.decision === "PENDING",
                                      ).length
                                    }
                                  </td>
                                  <td>{d.state.locks}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                      <p className="table-note">
                        События за весь сеанс, кроме отклонённых. Текущие
                        счётчики после разблокировки показаны в аудитории.
                      </p>
                    </article>
                  );
                })
              )}
            </div>
          )}
          {page === "devices" && (
            <>
              <div className="notice">
                <Download size={18} />
                <span>
                  Для работы без кабинета и подключения к серверу:{" "}
                  <a href="/api/student/download-offline">
                    скачать Qorgau Offline
                  </a>
                  . Настройка и управление выполняются на компьютере.
                </span>
              </div>
              <div className="notice">
                <Monitor size={18} />
                <span>
                  Студент устанавливает Qorgau или открывает EXE. Компьютер
                  автоматически появляется здесь — адрес и код вводить не нужно.
                </span>
              </div>
              {liveDevices.length === 0 ? (
                <Empty text="Нет работающих агентов. Запустите Qorgau на компьютере ученика." />
              ) : (
                <div className="panel table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Рабочее место</th>
                        <th>Связь</th>
                        <th>Камера</th>
                        <th>Возможности</th>
                        <th>Доступ</th>
                      </tr>
                    </thead>
                    <tbody>
                      {liveDevices.map((d) => (
                        <tr key={d.id}>
                          <td>
                            <strong>{d.name}</strong>
                            <small>{d.student}</small>
                          </td>
                          <td>
                            <Badge tone={d.online ? "green" : "red"}>
                              Подключён
                            </Badge>
                          </td>
                          <td>
                            {d.capabilities.camera &&
                            !d.capabilities.camera_fault
                              ? "Включена"
                              : "Включите в Qorgau"}
                          </td>
                          <td>
                            {!d.capabilities.desktop_monitor
                              ? "Обновите Qorgau"
                              : gazeEnabled(d)
                                ? "Телефон · лица · взгляд"
                                : "Телефон · лица. Взгляд выключен"}
                          </td>
                          <td>
                            {d.revoked_at
                              ? "Отозван"
                              : !d.simulated && (
                                  <button
                                    className="btn"
                                    disabled={
                                      busy || d.state.lifecycle === "RUNNING"
                                    }
                                    onClick={() => ask(d, "REVOKE")}
                                  >
                                    Отозвать доступ
                                  </button>
                                )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="fine">
                Если приложение закрыто или связь потеряна, компьютер исчезнет
                из списка в течение 6 секунд. История завершённых сеансов
                остаётся в отчётах.
              </p>
            </>
          )}
          {page === "teachers" && <TeacherFaces />}
          {page === "rules" && (
            <>
              <div className="notice">
                <ShieldCheck size={18} />
                <span>
                  Правила версии 3.0 зафиксированы для прототипа. Пороговые
                  значения видны ученику и преподавателю.
                </span>
              </div>
              <div className="rules-grid">
                <Rule
                  icon={<Eye />}
                  title="Три независимых счётчика"
                  text="Вниз, влево и вправо считаются отдельно. Непрерывный эпизод от 5 секунд даёт один балл. На третьем в одном направлении — блокировка по правилам."
                />
                <Rule
                  icon={<Smartphone />}
                  title="Телефон — критическое событие"
                  text="Два уверенных обнаружения на коротком интервале вызывают блокировку без накопления баллов. Наличие телефона не доказывает факт фотографирования."
                />
                <Rule
                  icon={<Users />}
                  title="Второе лицо — на проверку"
                  text="Появление второго лица на секунду и дольше сохраняется как спорный момент. Само по себе не блокирует ученика."
                />
                <Rule
                  icon={<History />}
                  title="Частые короткие отвлечения"
                  text="Три коротких эпизода за минуту общей длительностью от 6 секунд отправляются преподавателю. Не добавляют баллы за взгляд."
                />
                <Rule
                  icon={<Users />}
                  title="Лицо не видно"
                  text="Отсутствие лица от 3 секунд направляется на проверку; после 10 секунд возникает техническая причина приостановки. Это не автоматическое обвинение в нарушении."
                />
                <Rule
                  icon={<Smartphone />}
                  title="Вероятное наведение телефона"
                  text="Подъём и удержание телефона отмечаются отдельной эвристикой для ручной проверки. Направление объектива и факт снимка не определяются."
                />
                <Rule
                  icon={<UnlockKeyhole />}
                  title="Продолжение только по решению"
                  text="Возврат взгляда и отклонение события не снимают блокировку. Преподаватель разрешает продолжить с указанием причины. Начинается новый цикл счётчиков, история остаётся."
                />
                <Rule
                  icon={<Monitor />}
                  title="Внешний тест, отдельный контроль"
                  text="Перед стартом выберите сайт или приложение. На Windows доступно ограничение одного окна и горячих клавиш; для сайта выберите Qorgau Browser."
                />
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
          title={selectedDevice.student}
          subtitle={selectedDevice.name + " · " + "локальный агент"}
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
                  ? "Блокировка по правилам"
                  : selectedDevice.state.lifecycle === "RUNNING"
                    ? "Под контролем"
                    : selectedDevice.state.lifecycle === "COMPLETED"
                      ? "Сеанс завершён"
                      : "Ожидает начала"}
              </Badge>
              <span>Цикл {selectedDevice.state.epoch}</span>
              {selectedDevice.state.reason && (
                <span>
                  {eventNames[selectedDevice.state.reason] ||
                    "Решение преподавателя"}
                </span>
              )}
            </div>
            <ControlStatus device={selectedDevice} />
            {typeof selectedDevice.capabilities.model_version === "string" && (
              <p className="fine device-model-version">
                Модели: {selectedDevice.capabilities.model_version}
              </p>
            )}
            {gazeEnabled(selectedDevice) && <Counters d={selectedDevice} />}
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
                        ? "Разрешить продолжить"
                        : "Заблокировать"}
                    </button>
                    <button
                      className="btn"
                      onClick={() => ask(selectedDevice, "END_AND_RELEASE")}
                    >
                      Завершить контроль
                    </button>
                  </>
                )}
            </div>
            <h3 className="subheading">Записи этого компьютера</h3>
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
                        <video
                          key={media.id}
                          controls
                          preload="metadata"
                          src={media.url}
                          aria-label={`${eventNames[e.type] || e.type} — ${selectedDevice.name}`}
                          onLoadedMetadata={(v) => {
                            const offset = e.at - (media.clip_start || 0);
                            if (offset > 0 && offset < v.currentTarget.duration)
                              v.currentTarget.currentTime = offset;
                          }}
                        />
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
                        Проверить событие
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
                    LOCK: "Заблокировать",
                    UNLOCK: "Разрешить продолжить",
                    END_AND_RELEASE: "Завершить контроль",
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
          event={selectedEvent}
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
                ? "Разрешить продолжить?"
                : action.type === "LOCK"
                  ? "Заблокировать ученика?"
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
                  ? "Компьютер разблокирован. Тест продолжается."
                  : "Решение выполнено",
              );
            }}
          >
            <p>
              {action.type === "REVOKE"
                ? "Сохранённый токен компьютера перестанет работать. История сеансов сохранится; для нового подключения понадобится новая регистрация."
                : action.type === "UNLOCK"
                  ? "Начнётся новый цикл трёх счётчиков. Все события и решения сохранятся в истории."
                  : action.type === "END_AND_RELEASE"
                    ? "Контроль этого ученика завершится, блокировка будет снята. Внешний тест автоматически не отправляется."
                    : "Состояние будет заблокировано по решению преподавателя. В режиме наблюдения операционная система остаётся доступной."}
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
                  d.state.counts[id] >= n ? (n === 3 ? "red" : "amber") : ""
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
  icon,
  title,
  text,
}: {
  icon: ReactNode;
  title: string;
  text: string;
}) {
  return (
    <article className="panel rule">
      <span className="rule-icon">{icon}</span>
      <h2>{title}</h2>
      <p>{text}</p>
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
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Ученик / время</th>
            <th>Событие</th>
            <th>Видео</th>
            <th>Решение</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>
                <strong>{e.student}</strong>
                <small>
                  {e.device_name} · {clock(e.created_at)}
                </small>
              </td>
              <td>
                <strong>{eventNames[e.type] || e.type}</strong>
                <small>
                  {e.category === "CRITICAL"
                    ? "Критическое событие"
                    : "Наблюдение агента"}
                </small>
              </td>
              <td>
                {e.media.length ? (
                  <span className="with-icon">
                    <Video size={15} /> {e.media.length} фрагм.
                  </span>
                ) : (
                  <span className="muted">Нет записи</span>
                )}
              </td>
              <td>
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
              </td>
              <td>
                <button className="btn small" onClick={() => onSelect(e.id)}>
                  Проверить <ChevronRight size={14} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function NewExam({
  devices,
  onClose,
  onCreate,
}: {
  devices: Device[];
  onClose: () => void;
  onCreate: (body: unknown) => Promise<void>;
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
  const [studentNames, setStudentNames] = useState<Record<string, string>>({});
  const [title, setTitle] = useState("Контроль аудитории");
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
  return (
    <Modal
      title="Новый сеанс контроля"
      subtitle="Выберите среду тестирования и подготовленные компьютеры."
      onClose={onClose}
      wide
    >
      <form className="modal-body" onSubmit={submit}>
        <label>
          Название сеанса
          <input
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
                  "Одно окно без вкладок, переходы в пределах сайта",
                ],
                [
                  "WINDOW",
                  "Окно приложения",
                  "Окно программы, выбранное на компьютере ученика",
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
                <span>
                  <strong>{label}</strong>
                  <small>{description}</small>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        {environment === "BROWSER" && (
          <label>
            Адрес теста
            <input
              type="url"
              required
              pattern="https?://.+"
              value={testUrl}
              onChange={(e) => setTestUrl(e.target.value)}
              placeholder="https://example.kz/test"
              autoComplete="url"
            />
            <span className="field-help">
              Используйте прямую ссылку на тест. Переходы на другой сайт и новые
              окна блокируются.
            </span>
          </label>
        )}
        <h3 className="subheading">
          Компьютеры онлайн <span className="count">{chosen.length}</span>
        </h3>
        {!available.length ? (
          <div className="notice">
            Нет свободных компьютеров онлайн. Запустите Qorgau или завершите
            предыдущий сеанс.
          </div>
        ) : (
          <div className="check-list">
            {available.map((d) => (
              <div className="workstation-assignment" key={d.id}>
                <label className="workstation-choice">
                  <input
                    type="checkbox"
                    checked={chosen.some((c) => c.id === d.id)}
                    disabled={!ready(d)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, d.id]
                          : selected.filter((id) => id !== d.id),
                      )
                    }
                  />
                  <strong>{d.name}</strong>
                  {!ready(d) && (
                    <small>{preparationIssue(d, environment)}</small>
                  )}
                </label>
                <input
                  aria-label={`Ученик на ${d.name}`}
                  placeholder="Имя ученика (необязательно)"
                  maxLength={80}
                  disabled={!ready(d)}
                  value={studentNames[d.id] || ""}
                  onChange={(e) =>
                    setStudentNames({ ...studentNames, [d.id]: e.target.value })
                  }
                />
              </div>
            ))}
          </div>
        )}
        <div className="notice">
          <Eye size={17} />
          <span>
            {environment === "BROWSER"
              ? "На каждом компьютере откроется сайт теста в Qorgau Browser."
              : "Каждый компьютер откроет выбранное в Qorgau окно на весь экран."}{" "}
            При блокировке продолжить сможет только преподаватель. Ctrl+Alt+Q
            вызывает преподавателя.
          </span>
        </div>
        {chosen.some((d) => !gazeEnabled(d)) && (
          <div className="notice">
            У части компьютеров контроль взгляда выключен. Обновите приложение
            Qorgau и подготовьте контроль взгляда перед началом теста.
          </div>
        )}
        {error && <div className="error">{error}</div>}
        <div className="button-row end">
          <button className="btn" type="button" onClick={onClose}>
            Отмена
          </button>
          <button className="btn primary" disabled={busy || !chosen.length}>
            {busy ? (
              <LoaderCircle className="spin" size={17} />
            ) : (
              <Plus size={17} />
            )}{" "}
            Создать сеанс
          </button>
        </div>
      </form>
    </Modal>
  );
}
function EventReview({
  event: e,
  onClose,
  onUpdate,
  notify,
}: {
  event: Incident;
  onClose: () => void;
  onUpdate: () => Promise<void>;
  notify: (s: string) => void;
}) {
  const [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [index, setIndex] = useState(0);
  async function decide(decision: string) {
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
      subtitle={e.student + " · " + e.device_name + " · " + clock(e.created_at)}
      onClose={onClose}
      wide
    >
      <div className="modal-body">
        <div className="student-status">
          <Badge
            tone={
              e.decision === "PENDING"
                ? "amber"
                : e.decision === "REJECTED"
                  ? "green"
                  : "red"
            }
          >
            {decisionNames[e.decision]}
          </Badge>

          <span>Версия решения {e.revision}</span>
        </div>
        {e.media.length ? (
          <>
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
            {e.media.length > 1 && (
              <select
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
            {e.media[index]?.complete === false && (
              <p role="status">
                Запись неполная: отсутствует часть нужного интервала. Разрывы:{" "}
                {e.media[index]?.gaps
                  ?.map(([a, b]) => `${a.toFixed(1)}–${b.toFixed(1)} с`)
                  .join(", ") || "начало или конец фрагмента"}
                .
              </p>
            )}
          </>
        ) : (
          <div className="video-empty">
            <Video size={34} />
            <h3>Запись не прикреплена</h3>
            <p>
              {e.media_expired_at
                ? "Срок хранения записи истёк. Событие и решения сохранены."
                : "Агент ещё не передал фрагмент. Решение доступно, но визуального подтверждения пока нет."}
            </p>
          </div>
        )}
        <div className="evidence-info">
          <span>
            Начало: <strong>{e.start.toFixed(1)} с</strong>
          </span>
          <span>
            Срабатывание: <strong>{e.at.toFixed(1)} с</strong>
          </span>
          {e.duration && (
            <span>
              Длительность: <strong>{e.duration.toFixed(1)} с</strong>
            </span>
          )}
        </div>
        {e.type === "PHONE_AIM_REVIEW" && (
          <div className="notice evidence-context">
            <Smartphone size={18} />
            <span>
              Система отметила подъём и удержание телефона. По этому событию
              нельзя установить направление объектива или факт снимка —
              проверьте запись.
            </span>
          </div>
        )}
        <p className="fine">
          Время указано от начала контроля. Автоматическое событие — основание
          для проверки; оно не является доказательством нарушения само по себе.
        </p>
        <p className="fine">
          Видео автоматически удаляется через 2 часа после загрузки. Журнал
          событий и решения сохраняются.
        </p>
        <label>
          Комментарий к решению
          <textarea
            value={reason}
            maxLength={500}
            onChange={(x) => setReason(x.target.value)}
            placeholder="Что видно на записи и почему вы приняли это решение"
          />
        </label>
        <div className="button-row">
          <button
            className="btn"
            disabled={busy || !reason.trim()}
            onClick={() => decide("REJECTED")}
          >
            <X size={16} /> Отклонить событие
          </button>
          <button
            className="btn primary"
            disabled={busy || !reason.trim()}
            onClick={() => decide("CONFIRMED")}
          >
            <Check size={16} /> Подтвердить событие
          </button>
        </div>
        <p className="fine">
          Отклонение корректирует счётчик, но не разблокирует ученика
          автоматически.
        </p>
        {e.reviews.length > 0 && (
          <div className="review-log">
            <h3>История решений</h3>
            {[...e.reviews].reverse().map((r, i) => (
              <div key={i}>
                <strong>
                  {decisionNames[r.decision as keyof typeof decisionNames]} ·{" "}
                  {r.author}
                </strong>
                <small>{clock(r.at)}</small>
                <p>{r.reason}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}
