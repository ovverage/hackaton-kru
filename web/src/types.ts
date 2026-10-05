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
export type Target = { id: string; name: string; kind: "BROWSER" | "APP" };
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
  participants: Record<string, Device>;
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
  GAZE_DOWN: "Взгляд вниз",
  GAZE_LEFT: "Взгляд влево",
  GAZE_RIGHT: "Взгляд вправо",
  PHONE_DETECTED: "Обнаружен телефон",
  SECOND_FACE_REVIEW: "Второе лицо в кадре",
  FREQUENT_GAZE_REVIEW: "Частые отвлечения",
  CAMERA_UNAVAILABLE: "Камера недоступна",
  EXTENSION_DISCONNECTED: "Нет связи с расширением",
  BROWSER_ATTEMPT: "Смена вкладки или сайта",
  HEAD_TURN_REVIEW: "Поворот головы",
  FACE_ABSENCE_REVIEW: "Нет лица в кадре",
  FACE_ABSENCE_TECHNICAL: "Лицо отсутствует 10 секунд · техническая блокировка",
  PHONE_AIM_REVIEW: "Возможная попытка съёмки",
};
export const decisionNames = {
  PENDING: "На проверке",
  CONFIRMED: "Подтверждено",
  REJECTED: "Отклонено",
};
export const clock = (n: number) =>
  new Date(n * 1000).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
