import type { Device, Exam, Incident, Snapshot } from "../types";

const now = Math.floor(Date.now() / 1000);
const names = [
  "Айгерим Нурланова",
  "Данияр Сеитов",
  "Алина Ким",
  "Ерлан Жумабаев",
  "Мадина Оспанова",
  "Руслан Касымов",
  "Сауле Мусина",
  "Ильяс Садыков",
  "Диана Каримова",
  "Алихан Сериков",
  "Тимур Ахметов",
  "Аружан Тлеубаева",
  "Айсулу Ермекова",
  "Нурлан Абенов",
  "Жанна Ибраева",
  "Максат Рахимов",
  "София Ли",
  "Арман Бекетов",
  "Дарья Волкова",
  "Дана Каирбекова",
  "Мирас Омаров",
  "Индира Жаксылыкова",
  "Дамир Утегенов",
  "Камила Шарипова",
];
const devices: Device[] = names.map((student, index) => {
  const seat = index + 1,
    locked = seat === 7 || seat === 14,
    offline = seat === 11;
  return {
    id: `device-${seat}`,
    name: `K301-PC${String(seat).padStart(2, "0")}`,
    student,
    simulated: false,
    online: !offline,
    last_seen: now - (offline ? 80 : 1),
    exam_id: "exam-live",
    targets: [
      { id: "qorgau-browser", name: "Qorgau Browser", kind: "BROWSER" },
      {
        id: "primary-window",
        name: "LibreOffice Writer",
        kind: "APP",
        guardable: true,
      },
    ],
    capabilities: {
      camera: true,
      gaze: true,
      desktop_monitor: true,
      window_guard: true,
      guard_active: true,
      recording: true,
      selected_window: true,
      camera_fault: seat === 5,
      recording_tail: false,
      agent_version: "0.5.2",
      model_version: "YOLO11n / MediaPipe",
    },
    state: {
      lifecycle: seat === 20 ? "READY" : "RUNNING",
      access: locked ? "LOCKED" : "OPEN",
      reason: locked ? (seat === 14 ? "PHONE_DETECTED" : "GAZE_DOWN") : null,
      lock_id: locked ? `lock-${seat}` : null,
      version: 1,
      epoch: 1,
      locks: locked ? 1 : 0,
      counts: {
        DOWN: seat === 7 ? 3 : seat % 3,
        LEFT: seat % 4 === 0 ? 2 : 0,
        RIGHT: seat % 6 === 0 ? 1 : 0,
      },
    },
  };
});
const exam: Exam = {
  id: "exam-live",
  title: "Алгоритмы и структуры данных",
  group: "ИС-22",
  room: "К301",
  mode: "GUARDED",
  rule_version: "3.1",
  status: "RUNNING",
  created_at: now - 24 * 60,
  simulated: false,
  environment: {
    kind: "BROWSER",
    target_id: "qorgau-browser",
    url: "https://example.kz/test",
  },
  participants: Object.fromEntries(devices.map((d) => [d.id, d])),
};
// Development-only illustration: never used as real evidence or bundled in production.
const cameraIllustration =
  "data:image/svg+xml," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360"><defs><linearGradient id="bg" x2="0" y2="1"><stop stop-color="#697787"/><stop offset="1" stop-color="#343f4f"/></linearGradient></defs><rect width="640" height="360" fill="url(#bg)"/><path d="M0 264h640v96H0z" fill="#c9c9c7"/><path d="M80 0v260M540 0v260" stroke="#81909e" stroke-width="5"/><path d="M208 360q5-128 114-128t114 128" fill="#243759"/><ellipse cx="322" cy="152" rx="62" ry="82" fill="#cda78c"/><path d="M261 147q-18-98 58-104 79 0 65 113l-19-55q-50 18-95-1z" fill="#252a34"/><path d="M301 183q20 10 39 0" fill="none" stroke="#966f60" stroke-width="3"/><rect x="154" y="301" width="327" height="59" rx="8" fill="#919ba6"/><text x="20" y="25" fill="#eef2f8" font-size="12" font-family="sans-serif">Демонстрационный кадр</text></svg>`,
  );
const eventTypes = [
    "PHONE_DETECTED",
    "PHONE_AIM_REVIEW",
    "FREQUENT_GAZE_REVIEW",
    "SECOND_FACE_REVIEW",
    "GAZE_DOWN",
  ],
  eventSeats = [14, 14, 18, 4, 7];
const events: Incident[] = eventTypes.map((type, index) => {
  const device = devices[eventSeats[index] - 1];
  return {
    id: `event-${index + 1}`,
    exam_id: exam.id,
    device_id: device.id,
    student: device.student,
    device_name: device.name,
    type,
    category: type === "PHONE_DETECTED" ? "CRITICAL" : "REVIEW",
    direction: type === "GAZE_DOWN" ? "DOWN" : null,
    at: 1200 + index * 30,
    start: 1195 + index * 30,
    duration: 7 + index,
    created_at: now - 120 - index * 45,
    simulated: false,
    decision: "PENDING",
    revision: 0,
    retain_until: now + 3600,
    media: [
      {
        id: `media-${index}`,
        url: cameraIllustration,
        mime: "image/svg+xml",
        uploaded_at: now - 3600,
        expires_at: now + 3600,
        clip_start: 1190 + index * 30,
        clip_end: 1210 + index * 30,
        complete: index !== 1,
        gaps: index === 1 ? [[1190 + index * 30, 1192 + index * 30]] : [],
      },
    ],
    reviews: [],
  };
});
const previous: Exam[] = [1, 2].map((n) => ({
  ...exam,
  id: `exam-past-${n}`,
  title: n === 1 ? "Основы программирования" : "Базы данных",
  group: n === 1 ? "ИС-21" : "ИС-22",
  status: "COMPLETED",
  created_at: now - n * 86400,
  participants: Object.fromEntries(
    devices.slice(0, n === 1 ? 18 : 12).map((d) => [
      d.id,
      {
        id: d.id,
        name: d.name,
        student: d.student,
        state: {
          ...d.state,
          lifecycle: "COMPLETED",
          access: "OPEN",
          reason: null,
          lock_id: null,
        },
        simulated: false,
      },
    ]),
  ),
}));
const pastEvents: Incident[] = Array.from({ length: 8 }, (_, i) => {
  const template = events[i % 5],
    session = previous[i < 6 ? 0 : 1],
    decision = i === 0 ? "PENDING" : i % 3 === 1 ? "CONFIRMED" : "REJECTED";
  return {
    ...template,
    id: `past-event-${i}`,
    exam_id: session.id,
    created_at: session.created_at + 500 + i * 40,
    decision,
    revision: decision === "PENDING" ? 0 : 1,
    media: i < 4 ? template.media : [],
    media_expired_at: i < 4 ? undefined : now - 8000,
    retain_until: undefined,
    reviews:
      decision === "PENDING"
        ? []
        : [
            {
              author: "Сапарова А. К.",
              reason:
                decision === "CONFIRMED"
                  ? "На записи видно телефон во время теста."
                  : "Ученик посмотрел на преподавателя. Нарушения нет.",
              decision,
              at: session.created_at + 900 + i * 40,
            },
          ],
  };
});
export function scenario() {
  return new URLSearchParams(location.search).get("fixture") || "";
}
const active = scenario(),
  live = ["room", "device"].includes(active);
const prepared = devices.slice(0, 12).map((d, i) => ({
  ...d,
  online: true,
  exam_id: null,
  state: {
    ...d.state,
    lifecycle: "READY" as const,
    access: "OPEN" as const,
    reason: null,
    lock_id: null,
    locks: 0,
    counts: { DOWN: 0, LEFT: 0, RIGHT: 0 },
  },
  capabilities: {
    ...d.capabilities,
    camera: i !== 3,
    camera_fault: i === 3 || i === 10,
    window_guard: i !== 8,
    gaze: i !== 3 && i !== 10,
  },
}));
const snapshot: Snapshot =
  active === "empty"
    ? { devices: [], exams: [], events: [], commands: [] }
    : {
        devices: live ? devices : prepared,
        exams: live ? [exam, ...previous] : previous,
        events: live
          ? [...events, ...pastEvents]
          : [
              ...events.map((e) => ({ ...e, exam_id: previous[0].id })),
              ...pastEvents,
            ],
        commands: [],
      };
let authUser: { name: string } | null = ["login", "setup"].includes(active)
  ? null
  : { name: "Сапарова А. К." };
const faces = [
  { id: "teacher-1", name: "Айгуль Сапарова", created_at: now - 5 * 86400 },
  { id: "teacher-2", name: "Ерлан Касымов", created_at: now - 2 * 86400 },
];
const faceDevices = prepared.slice(0, 4).map((d, i) => ({
  id: d.id,
  name: d.name,
  enabled: i < 2,
  public_enrollment: false,
}));
export async function fixtureApi(
  path: string,
  body?: unknown,
): Promise<{ handled: boolean; value?: unknown }> {
  if (!active) return { handled: false };
  const input = (body || {}) as Record<string, unknown>;
  if (path === "/auth/status")
    return {
      handled: true,
      value: {
        setup_required: active === "setup" && !authUser,
        user: authUser,
      },
    };
  if (path === "/auth/logout") {
    authUser = null;
    return { handled: true, value: { ok: true } };
  }
  if (path.startsWith("/auth/")) {
    authUser = { name: String(input.name || "Сапарова А. К.") };
    return { handled: true, value: { user: authUser } };
  }
  if (path === "/snapshot")
    return { handled: true, value: structuredClone(snapshot) };
  if (path === "/teacher-faces")
    return { handled: true, value: { faces: structuredClone(faces) } };
  if (path === "/teacher-face-devices")
    return { handled: true, value: { devices: structuredClone(faceDevices) } };
  const access = path.match(/^\/devices\/([^/]+)\/teacher-face-access$/);
  if (access) {
    const device = faceDevices.find((d) => d.id === access[1]);
    if (device) device.enabled = Boolean(input.enabled);
    return { handled: true, value: { ok: true } };
  }
  const review = path.match(/^\/events\/([^/]+)\/review$/);
  if (review) {
    const event = snapshot.events.find((e) => e.id === review[1]);
    if (event) {
      if (input.expected_revision !== event.revision)
        throw new Error("Решение уже изменилось. Обновите событие.");
      event.decision = input.decision as Incident["decision"];
      event.revision++;
      event.reviews.push({
        author: authUser?.name || "Преподаватель",
        reason: String(input.reason),
        decision: event.decision,
        at: Math.floor(Date.now() / 1000),
      });
    }
    return { handled: true, value: { ok: true } };
  }
  const command = path.match(/^\/devices\/([^/]+)\/commands$/);
  if (command) {
    const device = snapshot.devices.find((d) => d.id === command[1]);
    if (!device) throw new Error("Компьютер не найден");
    const kind = String(input.type);
    if (kind === "START") device.state.lifecycle = "RUNNING";
    if (kind === "UNLOCK") {
      device.state.access = "OPEN";
      device.state.lock_id = null;
      device.state.reason = null;
      device.state.epoch++;
      device.state.counts = { DOWN: 0, LEFT: 0, RIGHT: 0 };
    }
    if (kind === "LOCK") {
      device.state.access = "LOCKED";
      device.state.reason = "TEACHER_REQUEST";
      device.state.lock_id = `lock-${Date.now()}`;
      device.state.locks++;
    }
    if (kind === "END_AND_RELEASE") {
      device.state.lifecycle = "COMPLETED";
      device.state.access = "OPEN";
      device.state.lock_id = null;
      device.state.reason = null;
    }
    device.state.version++;
    const current = snapshot.exams.find((e) => e.id === device.exam_id);
    if (current) {
      current.participants[device.id] = structuredClone(device);
      const participants = snapshot.devices.filter(
        (d) => d.exam_id === current.id,
      );
      current.status = participants.every(
        (d) => d.state.lifecycle === "COMPLETED",
      )
        ? "COMPLETED"
        : participants.some((d) => d.state.lifecycle === "RUNNING")
          ? "RUNNING"
          : "READY";
    }
    const result = {
      id: `command-${Date.now()}`,
      device_id: device.id,
      type: kind,
      status: "APPLIED",
    };
    snapshot.commands.push(result);
    return { handled: true, value: result };
  }
  const revoke = path.match(/^\/devices\/([^/]+)\/revoke$/);
  if (revoke) {
    const device = snapshot.devices.find((d) => d.id === revoke[1]);
    if (device) device.revoked_at = Math.floor(Date.now() / 1000);
    return { handled: true, value: { ok: true } };
  }
  if (path === "/exams" && body) {
    const id = `exam-${Date.now()}`,
      selected = snapshot.devices.filter((d) =>
        (input.device_ids as string[]).includes(d.id),
      );
    for (const device of selected) {
      device.exam_id = id;
      device.student =
        (input.student_names as Record<string, string>)[device.id] ||
        device.name;
      device.state.lifecycle = "READY";
    }
    const created: Exam = {
      ...exam,
      id,
      title: String(input.title),
      group: String(input.group),
      room: String(input.room),
      status: "READY",
      created_at: Math.floor(Date.now() / 1000),
      environment: input.environment as Exam["environment"],
      participants: Object.fromEntries(
        selected.map((d) => [d.id, structuredClone(d)]),
      ),
    };
    snapshot.exams.unshift(created);
    return { handled: true, value: structuredClone(created) };
  }
  return { handled: true, value: { status: "APPLIED" } };
}
export function fixtureView() {
  return {
    page:
      active === "history"
        ? "history"
        : active === "computers"
          ? "devices"
          : active === "rules"
            ? "rules"
            : active === "events" || active === "event"
              ? "review"
              : active === "teachers"
                ? "teachers"
                : "room",
    modal:
      active === "new"
        ? "new"
        : active === "device"
          ? "device"
          : active === "event"
            ? "event"
            : "",
  };
}
