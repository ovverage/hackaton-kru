import { translations } from "./translations.ts";

export type Language = "ru" | "kk" | "en";
export const languageNames: Record<Language, string> = {
  ru: "Русский",
  kk: "Қазақша",
  en: "English",
};
const storageKey = "qorgau.language";
const listeners = new Set<() => void>();
export function validLanguage(value: unknown): value is Language {
  return value === "ru" || value === "kk" || value === "en";
}
function initialLanguage(): Language {
  try {
    const saved = globalThis.localStorage?.getItem(storageKey);
    if (validLanguage(saved)) return saved;
  } catch {
    /* Storage may be blocked; the selector still works in memory. */
  }
  return "ru";
}
let language: Language = initialLanguage();
export function getLanguage(): Language {
  return language;
}
export function getLocale(): string {
  return { ru: "ru-RU", kk: "kk-KZ", en: "en-GB" }[language];
}

// Some packaged Chromium builds have a partial kk locale and emit "M10, Thu".
// Keep the small set of local-calendar labels used by this UI explicit.
const kazakhMonths = [
  "қаңтар",
  "ақпан",
  "наурыз",
  "сәуір",
  "мамыр",
  "маусым",
  "шілде",
  "тамыз",
  "қыркүйек",
  "қазан",
  "қараша",
  "желтоқсан",
];
const kazakhWeekdays = [
  "жексенбі",
  "дүйсенбі",
  "сейсенбі",
  "сәрсенбі",
  "бейсенбі",
  "жұма",
  "сенбі",
];
type DateOptions = Pick<
  Intl.DateTimeFormatOptions,
  "weekday" | "day" | "month" | "year"
>;
type TimeOptions = Pick<
  Intl.DateTimeFormatOptions,
  "hour" | "minute" | "second"
>;
const twoDigits = (value: number) => String(value).padStart(2, "0");
export function formatDate(date: Date, options: DateOptions = {}): string {
  if (!Number.isFinite(date.getTime())) return "—";
  if (language !== "kk") return date.toLocaleDateString(getLocale(), options);
  const fields: DateOptions = Object.keys(options).length
    ? options
    : { day: "2-digit", month: "2-digit", year: "numeric" };
  const numericMonth = fields.month === "numeric" || fields.month === "2-digit";
  const pieces = [
    fields.day
      ? fields.day === "2-digit"
        ? twoDigits(date.getDate())
        : String(date.getDate())
      : "",
    fields.month
      ? numericMonth
        ? fields.month === "2-digit"
          ? twoDigits(date.getMonth() + 1)
          : String(date.getMonth() + 1)
        : kazakhMonths[date.getMonth()]
      : "",
    fields.year
      ? fields.year === "2-digit"
        ? twoDigits(date.getFullYear() % 100)
        : String(date.getFullYear())
      : "",
  ].filter(Boolean);
  const label = pieces.join(numericMonth ? "." : " ");
  return fields.weekday
    ? `${kazakhWeekdays[date.getDay()]}${label ? ", " + label : ""}`
    : label;
}
export function formatTime(
  date: Date,
  options: TimeOptions = {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  },
): string {
  if (!Number.isFinite(date.getTime())) return "—";
  if (language !== "kk") return date.toLocaleTimeString(getLocale(), options);
  return [
    options.hour ? twoDigits(date.getHours()) : "",
    options.minute ? twoDigits(date.getMinutes()) : "",
    options.second ? twoDigits(date.getSeconds()) : "",
  ]
    .filter(Boolean)
    .join(":");
}
export function formatDateTime(date: Date): string {
  if (language !== "kk") return date.toLocaleString(getLocale());
  return `${formatDate(date)}, ${formatTime(date)}`;
}
function updateDocument() {
  if (typeof document === "undefined") return;
  document.documentElement.lang = language;
  document.title = t("Qorgau — кабинет преподавателя");
}
export function setLanguage(value: Language): void {
  if (!validLanguage(value)) return;
  language = value;
  try {
    globalThis.localStorage?.setItem(storageKey, value);
  } catch {}
  updateDocument();
  listeners.forEach((listener) => listener());
}
export function subscribeLanguage(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== storageKey) return;
    language = validLanguage(event.newValue) ? event.newValue : "ru";
    updateDocument();
    listeners.forEach((listener) => listener());
  });
  updateDocument();
}
/** Interpolate after translation so participant names, URLs and evidence are untouched. */
export function t(source: string, ...values: unknown[]): string {
  const entry = translations[source];
  const phrase =
    language === "ru" || !entry ? source : entry[language === "kk" ? 0 : 1];
  return phrase.replace(/\{(\d+)\}/g, (placeholder, index: string) =>
    Number(index) < values.length
      ? String(values[Number(index)] ?? "")
      : placeholder,
  );
}
/** Module-level event/decision tables must follow language switches without reloading. */
export function localizedRecord<T extends Record<string, string>>(
  source: T,
): T {
  return new Proxy(source, {
    get(target, key, receiver) {
      const value = Reflect.get(target, key, receiver);
      return typeof value === "string" ? t(value) : value;
    },
  });
}

/** Translate a stored notification again when the UI language changes. */
export function messageText(value: string): string {
  if (translations[value]) return t(value);
  for (const [source, variants] of Object.entries(translations)) {
    if (variants.includes(value)) return t(source);
  }
  return value;
}

/** Server data stays untouched; only known UI errors are translated. */
export function apiError(detail: unknown, status: number): string {
  if (typeof detail === "string") {
    if (translations[detail]) return t(detail);
    const separator = detail.indexOf(": ");
    if (separator > 0) {
      const suffix = detail.slice(separator + 2);
      if (translations[suffix])
        return detail.slice(0, separator + 2) + t(suffix);
    }
    if (detail.startsWith("Нужна чёткая фотография с одним видимым лицом."))
      return t("Нужна чёткая фотография с одним видимым лицом.");
    const duplicate = detail.match(
      /^Этот преподаватель уже добавлен: (.+)\. Используйте существующую запись\.$/,
    );
    if (duplicate)
      return t(
        "Этот преподаватель уже добавлен: {0}. Используйте существующую запись.",
        duplicate[1],
      );
    if (language === "ru") return detail;
  }
  const fallback: Record<number, string> = {
    401: "Войдите в панель преподавателя",
    403: "Доступ запрещён. Войдите снова или проверьте разрешения.",
    404: "Объект не найден",
    409: "Состояние изменилось. Обновите страницу и повторите действие.",
    422: "Проверьте заполненные поля и повторите действие.",
    429: "Слишком много попыток. Повторите через минуту.",
  };
  if (status >= 500) return t("Сервер временно недоступен. Повторите позже.");
  return fallback[status]
    ? t(fallback[status])
    : t("Ошибка запроса (HTTP {0}). Повторите действие.", status);
}
