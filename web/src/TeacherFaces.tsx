import { t, messageText, apiError, formatDate } from "./i18n.ts";
import { useEffect, useState, type FormEvent } from "react";
import { Camera, LoaderCircle, Trash2 } from "lucide-react";
import { api } from "./types";
import { Pill } from "./components/Design";
import "./teacherFaces.css";

type Teacher = { id: string; name: string; created_at: number };
type FaceDevice = {
  id: string;
  name: string;
  enabled: boolean;
  public_enrollment: boolean;
};

export default function TeacherFaces() {
  const [faces, setFaces] = useState<Teacher[]>([]);
  const [devices, setDevices] = useState<FaceDevice[]>([]);
  const [name, setName] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [fileKey, setFileKey] = useState(0);
  async function refresh() {
    const [result, access] = await Promise.all([
      api<{ faces: Teacher[] }>("/teacher-faces"),
      api<{ devices: FaceDevice[] }>("/teacher-face-devices"),
    ]);
    setFaces(result.faces);
    setDevices(access.devices);
  }
  useEffect(() => {
    refresh()
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
    const timer = window.setInterval(() => {
      refresh().catch((err: Error) => setError(err.message));
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);
  async function setDeviceAccess(device: FaceDevice) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api(
        `/devices/${encodeURIComponent(device.id)}/teacher-face-access`,
        {
          enabled: !device.enabled,
        },
      );
      await refresh();
      setMessage(
        device.enabled
          ? t(
              "Разрешение отозвано. Сохранённые в памяти образцы перестанут использоваться не позднее чем через минуту.",
            )
          : t(
              "Доступ компьютеру разрешён. Образцы появятся при следующем подключении к серверу.",
            ),
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function mutate(path: string, options: RequestInit) {
    const response = await fetch("/api/teacher-faces" + path, {
      ...options,
      headers: { "X-Requested-With": "Qorgau" },
    }).catch(() => {
      throw new Error(
        t("Нет связи с сервером. Проверьте интернет и повторите."),
      );
    });
    const result = await response.json();
    if (!response.ok) throw new Error(apiError(result.detail, response.status));
    await refresh();
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!photo || !name.trim()) return;
    setError("");
    setMessage("");
    if (photo.size > 3 * 1024 * 1024) {
      setError(t("Выберите фото до 3 МБ."));
      return;
    }
    setBusy(true);
    try {
      const body = new FormData();
      body.set("name", name.trim());
      body.set("image", photo);
      await mutate("", { method: "POST", body });
      setName("");
      setPhoto(null);
      setFileKey((value) => value + 1);
      setMessage(
        t(
          "Преподаватель добавлен. Проверка по лицу доступна на подключённых компьютерах.",
        ),
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(face: Teacher) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await mutate("/" + encodeURIComponent(face.id), { method: "DELETE" });
      setMessage(t("Образец «{0}» удалён.", face.name));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="teacher-faces">
      <div className="teacher-face-intro note ink">
        <Camera size={26} />
        <div>
          <h2>{t("Продолжение теста по лицу преподавателя")}</h2>
          <p>
            {t(
              "Добавьте фотографию преподавателя. На компьютере с тестом на паузе он смотрит в камеру, поворачивает голову по подсказке и возвращается в центр. Для проверки нужна связь с сервером; вход по паролю остаётся доступен.",
            )}
          </p>
          <p className="fine">
            {t(
              "На компьютерах с разрешённым доступом распознанный преподаватель не учитывается как второе лицо. Проверка движения — дополнительный шаг, она не гарантирует защиту от подмены изображения.",
            )}
          </p>
        </div>
      </div>
      {error && (
        <div className="teacher-face-feedback note error" role="alert">
          {messageText(error)}
        </div>
      )}
      {message && (
        <div className="teacher-face-feedback note ink" role="status">
          {messageText(message)}
        </div>
      )}
      <div className="teacher-face-columns">
        <form className="teacher-face-card card" onSubmit={submit}>
          <h3>{t("Добавить преподавателя")}</h3>
          <label className="field">
            {t("Имя")}
            <input
              className="input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              required
              disabled={busy}
              placeholder={t("Имя и фамилия")}
            />
          </label>
          <label className="field">
            {t("Фотография")}
            <span className="localized-file-picker">
              <span className="btn">{t("Выбрать фотографию")}</span>
              <span className="file-name">
                {photo?.name || t("Файл не выбран")}
              </span>
              <input
                className="localized-file-input"
                aria-label={t("Выбрать фотографию")}
                key={fileKey}
                type="file"
                accept="image/jpeg,image/png"
                onChange={(event) => setPhoto(event.target.files?.[0] ?? null)}
                required
                disabled={busy}
              />
            </span>
          </label>
          <p className="fine">
            {t(
              "JPEG или PNG до 3 МБ. Одно лицо анфас, без других людей в кадре. Хранится образец для сопоставления; исходное фото не сохраняется.",
            )}
          </p>
          <button
            className="btn primary"
            disabled={
              busy || loading || faces.length >= 20 || !photo || !name.trim()
            }
          >
            {busy ? (
              <LoaderCircle size={18} className="spin" />
            ) : (
              <Camera size={18} />
            )}{" "}
            {t("Добавить")}
          </button>
        </form>
        <div className="teacher-face-card card">
          <h3>
            {t("Преподаватели")}{" "}
            <span className="fine">{faces.length} / 20</span>
          </h3>
          {loading ? (
            <p role="status">{t("Загружаем список…")}</p>
          ) : faces.length === 0 ? (
            <p className="fine">{t("Пока нет добавленных преподавателей.")}</p>
          ) : (
            <ul className="teacher-face-list">
              {faces.map((face) => (
                <li key={face.id}>
                  <span className="teacher-initial" aria-hidden="true">
                    {face.name.trim().charAt(0)}
                  </span>
                  <div>
                    <strong>{face.name}</strong>
                    <small>
                      {t("Добавлен")}{" "}
                      {formatDate(new Date(face.created_at * 1000))}
                    </small>
                  </div>
                  <button
                    className="icon-btn"
                    disabled={busy}
                    onClick={() => void remove(face)}
                    aria-label={t("Удалить {0}", face.name)}
                  >
                    <Trash2 size={17} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <section className="teacher-face-card card">
        <h3>{t("Доступ компьютеров к образцам лиц")}</h3>
        <p className="fine">
          {t(
            "Исключение преподавателей из подсчёта лиц требует передачи компьютеру образцов для локального сопоставления. Сверьте имя и ID с приложением Qorgau и разрешите доступ только нужному компьютеру. Автоматическое подключение само по себе не даёт этого разрешения.",
          )}
        </p>
        <p className="fine">
          {t(
            "Проверка лица для продолжения теста выполняется на сервере отдельно. Локальный экзамен и локальный пароль работают без этого разрешения.",
          )}
        </p>
        {loading ? (
          <p role="status">{t("Загружаем компьютеры…")}</p>
        ) : devices.length === 0 ? (
          <p className="fine">{t("Подключённых компьютеров пока нет.")}</p>
        ) : (
          <div className="table-box teacher-access-table">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("Компьютер")}</th>
                  <th>{t("Доступ к образцам")}</th>
                  <th>
                    <span className="sr-only">{t("Действие")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {devices.map((device) => (
                  <tr key={device.id}>
                    <td>
                      <strong>{device.name}</strong>
                      <small>ID: {device.id}</small>
                      {device.enabled && !device.public_enrollment && (
                        <small>
                          {t("Подключён по коду или установщику преподавателя")}
                        </small>
                      )}
                    </td>
                    <td>
                      <Pill tone={device.enabled ? "ok" : "warn"}>
                        {device.enabled
                          ? t("Доступ разрешён")
                          : t("Ожидает разрешения")}
                      </Pill>
                    </td>
                    <td>
                      <button
                        className={`btn sm ${device.enabled ? "" : "primary"}`}
                        disabled={busy}
                        onClick={() => void setDeviceAccess(device)}
                      >
                        {device.enabled
                          ? t("Отозвать разрешение")
                          : t("Разрешить этому компьютеру")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  );
}
