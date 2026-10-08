import { t, messageText, formatDateTime } from "./i18n.ts";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Download, Copy, LoaderCircle } from "lucide-react";
import { api } from "./types";

type Package = {
  id: string;
  room: string;
  max_devices: number;
  used_devices: number;
  expires_at: number;
  revoked: boolean;
};
type CreatedPackage = Package & { download_path: string; download_url: string };
type PackageState = { ready: boolean; server: string; packages: Package[] };

export default function StudentPackages() {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<PackageState | null>(null);
  const [server, setServer] = useState("");
  const [room, setRoom] = useState("");
  const [count, setCount] = useState(50);
  const [hours, setHours] = useState(24);
  const [created, setCreated] = useState<CreatedPackage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (created) bodyRef.current?.scrollTo(0, 0);
  }, [created]);

  useEffect(() => {
    let active = true;
    api<PackageState>("/student-packages")
      .then((value) => {
        if (!active) return;
        setState(value);
        setServer(value.server);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);

  async function refresh() {
    setState(await api<PackageState>("/student-packages"));
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setCopied(false);
    try {
      const result = await api<CreatedPackage>("/student-packages", {
        server,
        room,
        max_devices: count,
        expires_hours: hours,
      });
      setCreated(result);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/student-packages/${id}/revoke`, {});
      if (created?.id === id) setCreated(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-body student-packages" ref={bodyRef}>
      <p>
        {t(
          "Один файл для всей аудитории. Студент скачивает EXE, открывает его — компьютер появляется в вашем кабинете. Адрес и код вводить не нужно.",
        )}
      </p>
      {error && (
        <div className="notice" role="alert">
          {messageText(error)}
        </div>
      )}
      {!state && !error && (
        <p role="status">
          <LoaderCircle size={16} /> {t("Загружаем настройки…")}
        </p>
      )}
      {state && (
        <>
          {!state.ready && (
            <div className="notice" role="status">
              {t(
                "Сборка EXE ещё не загружена на сервер. После её подготовки здесь можно будет скачать приложение для аудитории.",
              )}
            </div>
          )}
          {!created && (
            <form onSubmit={create}>
              <label>
                {t("Аудитория")}
                <input
                  required
                  maxLength={80}
                  value={room}
                  onChange={(e) => setRoom(e.target.value)}
                  placeholder={t("Например, 301")}
                />
              </label>
              <div className="package-fields">
                <label>
                  {t("Количество компьютеров")}
                  <input
                    type="number"
                    min={1}
                    max={100}
                    required
                    value={count}
                    onChange={(e) => setCount(Number(e.target.value))}
                  />
                </label>
                <label>
                  {t("Срок первого подключения")}
                  <select
                    value={hours}
                    onChange={(e) => setHours(Number(e.target.value))}
                  >
                    <option value={24}>{t("24 часа")}</option>
                    <option value={72}>{t("3 дня")}</option>
                    <option value={168}>{t("7 дней")}</option>
                  </select>
                </label>
              </div>
              <label>
                {t("Адрес сервера для компьютеров")}
                <input
                  type="url"
                  required
                  value={server}
                  onChange={(e) => setServer(e.target.value)}
                  placeholder="https://qorgau.example.kz"
                />
              </label>
              <p className="fine">
                {t(
                  "Укажите доступный студентам HTTPS-адрес этого сайта. После размещения на сервере он подставится автоматически. Localhost подходит только для проверки на одном ПК.",
                )}
              </p>
              <button
                className="btn primary full"
                disabled={busy || !state.ready}
                type="submit"
              >
                {busy ? <LoaderCircle size={17} /> : <Download size={17} />}{" "}
                {t("Подготовить EXE")}
              </button>
            </form>
          )}
          {created && (
            <section className="package-download" aria-label={t("Готовый EXE")}>
              <h3>
                {t("Приложение для аудитории")} {created.room} {t("готово")}
              </h3>
              <p>
                {t(
                  "Передайте этот файл всем студентам или отправьте ссылку на скачивание. Сохраните её перед закрытием окна.",
                )}
              </p>
              <a
                className="btn primary full"
                href={created.download_path}
                download="Qorgau-Classroom.exe"
              >
                <Download size={17} /> {t("Скачать EXE для студентов")}
              </a>
              <label>
                {t("Ссылка для студентов")}
                <input
                  readOnly
                  value={created.download_url}
                  onFocus={(e) => e.target.select()}
                />
              </label>
              <button
                className="btn full"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(created.download_url);
                    setCopied(true);
                  } catch {
                    setError(t("Выделите и скопируйте ссылку вручную"));
                  }
                }}
              >
                <Copy size={16} />{" "}
                {copied ? t("Ссылка скопирована") : t("Скопировать ссылку")}
              </button>
            </section>
          )}
          {created && (
            <button className="btn full" onClick={() => setCreated(null)}>
              {t("Подготовить ещё один EXE")}
            </button>
          )}
          <p className="fine">
            {t(
              "Лимит и срок действуют только на новые подключения. Зарегистрированные компьютеры продолжат работать и получать сеансы после окончания срока.",
            )}
          </p>
          {state.packages.length > 0 && (
            <section className="package-list">
              <h3>{t("Пакеты подключения")}</h3>
              <button
                className="btn"
                disabled={busy}
                onClick={() => refresh().catch((e) => setError(e.message))}
              >
                {t("Обновить подключения")}
              </button>
              {state.packages.map((p) => {
                const active = !p.revoked && p.expires_at * 1000 > Date.now();
                return (
                  <div className="package-row" key={p.id}>
                    <div>
                      <strong>
                        {t("Аудитория")} {p.room}
                      </strong>
                      <p>
                        {p.used_devices} {t("из")} {p.max_devices} {t("ПК ·")}{" "}
                        {p.revoked
                          ? t("Отключён")
                          : active
                            ? t(
                                "до {0}",
                                formatDateTime(new Date(p.expires_at * 1000)),
                              )
                            : t("Срок истёк")}
                      </p>
                    </div>
                    {active && (
                      <button
                        className="btn"
                        disabled={busy}
                        onClick={() => revoke(p.id)}
                      >
                        {t("Отключить пакет")}
                      </button>
                    )}
                  </div>
                );
              })}
            </section>
          )}
        </>
      )}
    </div>
  );
}
