import { t, localizedRecord } from "./i18n.ts";
import { api, type Device, type Exam, type Snapshot } from "./types.ts";

export type CommandType = "START" | "LOCK" | "UNLOCK" | "END_AND_RELEASE";
type Target = Pick<Device, "id" | "name" | "exam_id" | "state">;
type Receive = (snapshot: Snapshot) => void;
type Wait = () => Promise<void>;
const pause: Wait = () => new Promise((resolve) => setTimeout(resolve, 1000));

const failures: Record<string, string> = localizedRecord({
  PHONE_STILL_PRESENT:
    "Телефон всё ещё в кадре. Уберите его и повторите продолжение.",
  FACE_STILL_ABSENT: "Лицо не видно. Вернитесь в кадр и повторите продолжение.",
  CAMERA_UNAVAILABLE: "Камера недоступна. Восстановите её в приложении.",
  CAMERA_PREPARING:
    "Камера ещё запускается. Дождитесь готовности в приложении.",
  CAMERA_REQUIRED:
    "Включите камеру в приложении студента перед началом контроля.",
  CAMERA_NOT_READY: "Камера и запись не готовы. Проверьте приложение студента.",
  CAMERA_FRAME_STALE:
    "Изображение камеры не обновляется. Перезапустите камеру в приложении.",
  NEED_EXACTLY_ONE_FACE:
    "Перед началом контроля в кадре должно быть одно лицо.",
  REMOVE_PHONE_BEFORE_START: "Уберите телефон из кадра перед началом контроля.",
  NEED_1GB_RECORDING_SPACE:
    "На компьютере нужно освободить хотя бы 1 ГБ для записи.",
  WINDOW_GUARD_UNAVAILABLE:
    "Защита окна не готова. Проверьте приложение на компьютере.",
  TARGET_UNAVAILABLE:
    "Окно теста недоступно. Выберите его заново в приложении.",
  TARGET_CLOSED:
    "Окно теста закрыто. Завершите этот сеанс и выберите окно заново.",
  STATE_CONFLICT:
    "Состояние компьютера изменилось. Обновите карточку и повторите решение.",
  SESSION_MISMATCH:
    "На компьютере уже другой сеанс. Откройте актуальный сеанс.",
  COMMAND_EXPIRED:
    "Компьютер не успел получить команду. Проверьте связь и повторите.",
  LOCK_MISMATCH: "Возникла новая блокировка. Проверьте её причину.",
  SESSION_NOT_RUNNING: "Контроль ещё не запущен. Сначала начните сеанс.",
  SESSION_COMPLETED: "Этот сеанс уже завершён. Создайте новый сеанс.",
  INVALID_URL: "Адрес теста недоступен. Проверьте выбранное окно в приложении.",
});

export function commandFailure(
  error: string | undefined,
  expired = false,
): string {
  return (
    failures[error || ""] ||
    (expired
      ? t("Компьютер не ответил. Проверьте связь и повторите.")
      : error || t("Команда не выполнена"))
  );
}

export async function sendDeviceCommand(
  target: Target,
  type: CommandType,
  reason: string,
  receive: Receive,
  wait: Wait = pause,
): Promise<void> {
  const snapshot = await api<Snapshot>("/snapshot");
  receive(snapshot);
  const current = snapshot.devices.find((device) => device.id === target.id);
  if (!current || !target.exam_id || current.exam_id !== target.exam_id)
    throw new Error(t("Сеанс компьютера изменился. Откройте его заново."));
  if (type === "END_AND_RELEASE" && current.state.lifecycle === "COMPLETED")
    return;
  if (type === "UNLOCK" && current.state.lock_id !== target.state.lock_id)
    throw new Error(t("Возникла новая блокировка. Проверьте её причину."));
  const sent = await api<Snapshot["commands"][number]>(
    `/devices/${target.id}/commands`,
    {
      type,
      exam_id: target.exam_id,
      expected_version: current.state.version,
      lock_id: current.state.lock_id,
      reason,
    },
  );
  if (sent.status === "APPLIED") return;
  for (let attempt = 0; attempt < 32; attempt++) {
    await wait();
    const fresh = await api<Snapshot>("/snapshot");
    receive(fresh);
    const ack = fresh.commands.find((command) => command.id === sent.id);
    if (ack?.status === "APPLIED") return;
    if (ack && ["REJECTED", "EXPIRED"].includes(ack.status))
      throw new Error(commandFailure(ack.error, ack.status === "EXPIRED"));
  }
  throw new Error(
    t("Компьютер пока не подтвердил команду. Проверьте связь и состояние."),
  );
}

export async function sendGroupCommand(
  targets: Target[],
  type: CommandType,
  reason: string,
  receive: Receive,
  wait: Wait = pause,
): Promise<void> {
  const results = await Promise.allSettled(
    targets.map((target) =>
      sendDeviceCommand(target, type, reason, receive, wait),
    ),
  );
  const errors = results.flatMap((result, index) =>
    result.status === "rejected"
      ? [
          `${targets[index].name}: ${result.reason instanceof Error ? result.reason.message : t("Команда не выполнена")}`,
        ]
      : [],
  );
  if (errors.length)
    throw new Error(
      t(
        "Не выполнили команду {0} из {1} компьютеров. {2}{3}",
        errors.length,
        targets.length,
        errors.slice(0, 3).join(" "),
        errors.length > 3 ? t(" Ещё ошибок: {0}.", errors.length - 3) : "",
      ),
    );
}

export async function endExam(
  exam: Exam,
  receive: Receive,
  wait: Wait = pause,
): Promise<void> {
  const targets = Object.values(exam.participants)
    .filter((participant) => participant.state.lifecycle !== "COMPLETED")
    .map((participant) => ({ ...participant, exam_id: exam.id }));
  await sendGroupCommand(
    targets,
    "END_AND_RELEASE",
    t("Преподаватель завершил сеанс"),
    receive,
    wait,
  );
}
