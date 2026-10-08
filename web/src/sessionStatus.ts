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
  if (!device.online) return "Нет связи с компьютером";
  if (!c.window_guard) return "Нужен Windows-агент с защитой окна";
  // Current agents queue camera, calibration and target selection on the workstation.
  if (c.interactive_start) return null;
  if (!c.camera || c.camera_fault) return "Подготовьте камеру в Qorgau";
  if (!c.recording) return "Запись видео не готова";
  if (environment === "BROWSER") {
    return device.targets.some(
      (target) => target.id === "qorgau-browser" && target.kind === "BROWSER",
    )
      ? null
      : "Обновите агент: Qorgau Browser недоступен";
  }
  const target = device.targets.find((item) => item.id === "primary-window");
  if (!c.desktop_monitor || !c.selected_window || !target)
    return "Выберите окно теста в Qorgau";
  if (target.guardable === false) return "Для сайта используйте Qorgau Browser";
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
        ? "Сеанс завершён"
        : "Нет актуальных данных"
      : detail,
    tone: !current ? "idle" : ready ? "ready" : "attention",
  });
  return [
    signal(
      "Камера",
      Boolean(c.camera && !c.camera_fault),
      c.camera && !c.camera_fault ? "Камера готова" : "Камера недоступна",
    ),
    signal(
      "Взгляд",
      gazeEnabled(device),
      gazeEnabled(device)
        ? "Контроль взгляда включён"
        : "Контроль взгляда недоступен",
    ),
    signal(
      "Защита",
      Boolean(
        c.window_guard && !c.guard_fault && (running ? c.guard_active : true),
      ),
      !c.window_guard
        ? "Защита ввода недоступна"
        : c.guard_fault
          ? "Проверьте защиту окна"
          : running
            ? c.guard_active
              ? "Защита ввода активна"
              : "Защита ввода не подтверждена"
            : "Защита включится при старте",
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
