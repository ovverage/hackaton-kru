export type Direction = "DOWN" | "LEFT" | "RIGHT";
export type SessionState = {
  lifecycle: "READY" | "RUNNING" | "COMPLETED";
  access: "OPEN" | "LOCKED";
  reason: string | null;
  lock_id: string | null;
  version: number;
  epoch: number;
  locks: number;
  counts: Record<Direction, number>;
};
export type Target = {
  id: string;
  name: string;
  kind: "BROWSER" | "APP";
  guardable?: boolean;
};
export type Device = {
  id: string;
  name: string;
  student: string;
  state: SessionState;
  simulated: boolean;
  online: boolean;
  last_seen: number;
  exam_id: string | null;
  targets: Target[];
  capabilities: Record<string, unknown>;
  revoked_at?: number;
};
// Historical exam entries are deliberately smaller than live device records.
export type Participant = Pick<
  Device,
  "id" | "name" | "student" | "state" | "simulated"
> & {
  last_seen?: number;
};
export function gazeEnabled(device: Device): boolean {
  return (
    Boolean(device.capabilities.camera) &&
    !device.capabilities.camera_fault &&
    (device.capabilities.gaze === true ||
      (device.capabilities.gaze === undefined &&
        [
          "yolo11n-onnx/mediapipe-personal-calibration",
          "experimental-calibrated-iris",
        ].includes(String(device.capabilities.vision))))
  );
}
export type Exam = {
  id: string;
  title: string;
  group: string;
  room: string;
  mode: string;
  status: string;
  created_at: number;
  simulated: boolean;
  environment: { kind: string; target_id: string; url?: string };
  participants: Record<string, Participant>;
};
export type Incident = {
  id: string;
  exam_id: string;
  device_id: string;
  student: string;
  device_name: string;
  type: string;
  category: string;
  direction: string | null;
  at: number;
  start: number;
  end?: number;
  duration?: number;
  created_at: number;
  simulated: boolean;
  decision: "PENDING" | "CONFIRMED" | "REJECTED";
  revision: number;
  retain_until?: number;
  media_expired_at?: number;
  media: {
    id: string;
    url: string;
    mime: string;
    clip_start?: number;
    clip_end?: number;
    complete?: boolean | null;
    gaps?: [number, number][];
  }[];
  reviews: { author: string; reason: string; decision: string; at: number }[];
};
export type Snapshot = {
  devices: Device[];
  exams: Exam[];
  events: Incident[];
  commands: {
    id: string;
    device_id: string;
    type: string;
    status: string;
    error?: string;
  }[];
};
export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  if (import.meta.env?.DEV) {
    const { fixtureApi } = await import("./dev/fixtures");
    const result = await fixtureApi(path);
    if (result.handled) return result.value as T;
  }
  const response = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "Qorgau",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let message = "Не удалось выполнить действие";
    try {
      const data = await response.json();
      message = typeof data.detail === "string" ? data.detail : message;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}
export const eventNames: Record<string, string> = {
  TEACHER_REQUEST: "Ученик позвал преподавателя",
  GAZE_DOWN: "Взгляд вниз",
  GAZE_LEFT: "Взгляд влево",
  GAZE_RIGHT: "Взгляд вправо",
  PHONE_DETECTED: "Телефон в кадре",
  PHONE_LOCKED_REVIEW: "Телефон во время паузы",
  SECOND_FACE_REVIEW: "Второе лицо в кадре",
  FREQUENT_GAZE_REVIEW: "Частые отвлечения",
  CAMERA_UNAVAILABLE: "Камера недоступна",
  EXTENSION_DISCONNECTED: "Нет связи с расширением",
  BROWSER_ATTEMPT: "Попытка открыть другой сайт",
  HEAD_TURN_REVIEW: "Поворот головы",
  FACE_ABSENCE_REVIEW: "Лица не видно",
  TARGET_CLOSED: "Окно теста закрыто",
  REMOTE_SESSION: "Удалённый рабочий стол",
  ENVIRONMENT_ATTEMPT: "Попытка выйти из теста",
  SERVER_UNAVAILABLE: "Нет связи с сервером",
  GUARD_UNAVAILABLE: "Защита окна не работает",
  DISPLAY_CHANGED: "Изменилось число экранов",
  CAMERA_FROZEN: "Камера замерла",
  AGENT_RESTARTED: "Qorgau перезапущен",
  AGENT_FAILURE: "Сбой Qorgau",

  FACE_ABSENCE_TECHNICAL: "Лица не видно 10 секунд",
  PHONE_AIM_REVIEW: "Возможная съёмка телефоном",
};
export const decisionNames = {
  PENDING: "Ждёт решения",
  CONFIRMED: "Нарушение",
  REJECTED: "Нарушения нет",
};
export const clock = (n: number) =>
  new Date(n * 1000).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
