import { t } from "./i18n.ts";
import { gazeEnabled, type Device, type Incident } from "./types.ts";

export type TestEnvironment = "BROWSER" | "WINDOW";
export type ControlSignal = {
  label: string;
  detail: string;
  tone: "ready" | "attention" | "idle";
};

export function preparationIssue(
  device: Device,
  environment: TestEnvironment,
): string | null {
  const c = device.capabilities;
  if (!device.online) return t("Нет связи с компьютером");
  if (!c.window_guard) return t("Нужен Windows-агент с защитой окна");
  // Current agents queue camera, calibration and target selection on the workstation.
  if (c.interactive_start) return null;
  if (!c.camera || c.camera_fault) return t("Подготовьте камеру в Qorgau");
  if (!c.recording) return t("Запись видео не готова");
  if (environment === "BROWSER") {
    return device.targets.some(
      (target) => target.id === "qorgau-browser" && target.kind === "BROWSER",
    )
      ? null
      : t("Обновите агент: Qorgau Browser недоступен");
  }
  const target = device.targets.find((item) => item.id === "primary-window");
  if (!c.desktop_monitor || !c.selected_window || !target)
    return t("Выберите окно теста в Qorgau");
  if (target.guardable === false)
    return t("Для сайта используйте Qorgau Browser");
  return null;
}

export function controlSignals(device: Device): ControlSignal[] {
  const c = device.capabilities;
  const running = device.state.lifecycle === "RUNNING";
  const current = device.online && device.state.lifecycle !== "COMPLETED";
  const signal = (
    label: string,
    ready: boolean,
    detail: string,
  ): ControlSignal => ({
    label,
    detail: !current
      ? device.online
        ? t("Сеанс завершён")
        : t("Нет актуальных данных")
      : detail,
    tone: !current ? "idle" : ready ? "ready" : "attention",
  });
  return [
    signal(
      t("Камера"),
      Boolean(c.camera && !c.camera_fault),
      c.camera && !c.camera_fault ? t("Камера готова") : t("Камера недоступна"),
    ),
    signal(
      t("Взгляд"),
      gazeEnabled(device),
      gazeEnabled(device)
        ? t("Контроль взгляда включён")
        : t("Контроль взгляда недоступен"),
    ),
    signal(
      t("Защита"),
      Boolean(
        c.window_guard && !c.guard_fault && (running ? c.guard_active : true),
      ),
      !c.window_guard
        ? t("Защита ввода недоступна")
        : c.guard_fault
          ? t("Проверьте защиту окна")
          : running
            ? c.guard_active
              ? t("Защита ввода активна")
              : t("Защита ввода не подтверждена")
            : t("Защита включится при старте"),
    ),
  ];
}

export function sessionSummary(devices: Device[], events: Incident[]) {
  return {
    online: devices.filter((device) => device.online).length,
    running: devices.filter(
      (device) =>
        device.online &&
        device.state.lifecycle === "RUNNING" &&
        device.state.access !== "LOCKED",
    ).length,
    locked: devices.filter((device) => device.state.access === "LOCKED").length,
    pending: events.filter((event) => event.decision === "PENDING").length,
  };
}

export function pendingEvents(events: Incident[], limit = 5): Incident[] {
  return events
    .filter((event) => event.decision === "PENDING")
    .sort((a, b) => b.created_at - a.created_at)
    .slice(0, limit);
}
