import type { Device, Exam, Incident, Snapshot } from "../types";

const now = Math.floor(Date.now() / 1000);
const names = [
  "Айгерим Нурланова", "Данияр Алиев", "Алина Ким", "Ерлан Жумабаев", "Мадина Оспанова", "Руслан Касымов",
  "Сауле Мусина", "Ильяс Садыков", "Диана Каримова", "Алихан Сериков", "Тимур Ахметов", "Аружан Тлеубаева",
  "Айсулу Ермекова", "Нурлан Абенов", "Жанна Ибраева", "Максат Рахимов", "София Ли", "Арман Бекетов",
  "Дарья Волкова", "Дана Каирбекова", "Мирас Омаров", "Индира Жаксылыкова", "Дамир Утегенов", "Камила Шарипова",
];

const devices: Device[] = names.map((student, index) => {
  const seat = index + 1;
  const locked = seat === 7 || seat === 14;
  const offline = seat === 11;
  const ready = seat === 20;
  return {
    id: `device-${seat}`,
    name: `K301-PC${String(seat).padStart(2, "0")}`,
    student,
    simulated: false,
    online: !offline,
    last_seen: now - (offline ? 80 : 1),
    exam_id: "exam-live",
    targets: [],
    capabilities: {
      camera: true,
      gaze: true,
      desktop_monitor: true,
      guard_active: true,
      camera_fault: seat === 5,
      recording_tail: false,
    },
    state: {
      lifecycle: ready ? "READY" : "RUNNING",
      access: locked ? "LOCKED" : "OPEN",
      reason: locked ? (seat === 14 ? "PHONE_DETECTED" : "GAZE_DOWN") : null,
      lock_id: locked ? `lock-${seat}` : null,
      version: 1,
      epoch: locked ? 2 : 1,
      locks: locked ? 1 : 0,
      counts: { DOWN: seat % 3, LEFT: seat % 4 === 0 ? 2 : 0, RIGHT: seat % 6 === 0 ? 1 : 0 },
    },
  };
});

const exam: Exam = {
  id: "exam-live",
  title: "Алгоритмы и структуры данных",
  group: "Рубежный контроль 2, группа ИС-22",
  room: "К301",
  mode: "GUARDED",
  status: "RUNNING",
  created_at: now - 24 * 60,
  simulated: false,
  environment: { kind: "BROWSER", target_id: "qorgau-browser", url: "https://example.kz/test" },
  participants: Object.fromEntries(devices.map((device) => [device.id, device])),
};

const eventTypes = ["PHONE_DETECTED", "PHONE_AIM_REVIEW", "FREQUENT_GAZE_REVIEW", "SECOND_FACE_REVIEW", "GAZE_DOWN"];
const eventSeats = [14, 14, 18, 4, 7];
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
    at: 1200 + index * 60,
    start: 1195 + index * 60,
    duration: 7 + index,
    created_at: now - 120 - index * 45,
    simulated: false,
    decision: "PENDING",
    revision: 0,
    media: [{ id: `media-${index}`, url: "", mime: "video/mp4", clip_start: 1190 + index * 60, clip_end: 1210 + index * 60, complete: true, gaps: [] }],
    reviews: [],
  };
});

const snapshot: Snapshot = { devices, exams: [exam], events, commands: [] };

export function scenario() {
  const value = new URLSearchParams(location.search).get("fixture") || "";
  return value;
}

export async function fixtureApi(path: string): Promise<{ handled: boolean; value?: unknown }> {
  const active = scenario();
  if (!active) return { handled: false };
  if (path === "/auth/status") return { handled: true, value: active === "login" ? { setup_required: false, user: null } : { setup_required: false, user: { name: "Сапарова А. К." } } };
  if (path.startsWith("/auth/")) return { handled: true, value: { user: { name: "Сапарова А. К." } } };
  if (path === "/snapshot") return { handled: true, value: active === "empty" ? { devices: [], exams: [], events: [], commands: [] } : snapshot };
  return { handled: true, value: { status: "APPLIED" } };
}

export function fixtureView() {
  const active = scenario();
  return {
    page: active === "history" ? "history" : active === "computers" ? "devices" : active === "rules" ? "rules" : active === "events" || active === "event" ? "review" : "room",
    modal: active === "new" ? "new" : active === "device" ? "device" : active === "event" ? "event" : "",
  };
}
