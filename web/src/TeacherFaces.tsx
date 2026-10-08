import { useEffect, useState, type FormEvent } from "react";
import { Camera, LoaderCircle, Trash2, UserRound } from "lucide-react";
import { api } from "./types";
import "./teacherFaces.css";

type Teacher = { id: string; name: string; created_at: number };
type FaceDevice = { id: string; name: string; enabled: boolean; public_enrollment: boolean };

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
      await api(`/devices/${encodeURIComponent(device.id)}/teacher-face-access`, {
        enabled: !device.enabled,
      });
      await refresh();
      setMessage(device.enabled
        ? "Разрешение отозвано. Сохранённые в памяти образцы перестанут использоваться не позднее чем через минуту."
        : "Доступ компьютеру разрешён. Образцы появятся при следующем подключении к серверу.");
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
    });
    const result = await response.json();
    if (!response.ok)
      throw new Error(
        typeof result.detail === "string"
          ? result.detail
          : "Не удалось сохранить изменения",
      );
    await refresh();
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!photo || !name.trim()) return;
    setError("");
    setMessage("");
    if (photo.size > 3 * 1024 * 1024) {
      setError("Выберите фото до 3 МБ.");
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
        "Преподаватель добавлен. Проверка по лицу доступна на подключённых компьютерах.",
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
      setMessage(`Образец «${face.name}» удалён.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="teacher-faces">
      <div className="teacher-face-intro">
        <Camera size={26} />
        <div>
          <h2>Разблокировка преподавателем</h2>
          <p>
            Добавьте фотографию преподавателя. На заблокированном компьютере он
            смотрит в камеру, поворачивает голову по подсказке и возвращается в
            центр. Для проверки нужна связь с сервером; вход по паролю остаётся
            доступен.
          </p>
          <p className="fine">
            На компьютерах с разрешённым доступом распознанный преподаватель не учитывается как второе лицо. Проверка
            движения — дополнительный шаг, она не гарантирует защиту от подмены
            изображения.
          </p>
        </div>
      </div>
      {error && (
        <div className="teacher-face-feedback error" role="alert">
          {error}
        </div>
      )}
      {message && (
        <div className="teacher-face-feedback" role="status">
          {message}
        </div>
      )}
      <div className="teacher-face-columns">
        <form className="teacher-face-card" onSubmit={submit}>
          <h3>Добавить преподавателя</h3>
          <label>
            Имя
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              required
              disabled={busy}
              placeholder="Имя и фамилия"
            />
          </label>
          <label>
            Фотография
            <input
              key={fileKey}
              type="file"
              accept="image/jpeg,image/png"
              onChange={(event) => setPhoto(event.target.files?.[0] ?? null)}
              required
              disabled={busy}
            />
          </label>
          <p className="fine">
            JPEG или PNG до 3 МБ. Одно лицо анфас, без других людей в кадре.
            Хранится образец для сопоставления; исходное фото не сохраняется.
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
            Добавить
          </button>
        </form>
        <div className="teacher-face-card">
          <h3>
            Преподаватели <span className="fine">{faces.length} / 20</span>
          </h3>
          {loading ? (
            <p role="status">Загружаем список…</p>
          ) : faces.length === 0 ? (
            <p className="fine">Пока нет добавленных преподавателей.</p>
          ) : (
            <ul className="teacher-face-list">
              {faces.map((face) => (
                <li key={face.id}>
                  <UserRound size={22} />
                  <div>
                    <strong>{face.name}</strong>
                    <small>
                      Добавлен{" "}
                      {new Date(face.created_at * 1000).toLocaleDateString(
                        "ru",
                      )}
                    </small>
                  </div>
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() => void remove(face)}
                    aria-label={`Удалить ${face.name}`}
                  >
                    <Trash2 size={17} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <section className="teacher-face-card">
        <h3>Доступ компьютеров к образцам лиц</h3>
        <p className="fine">
          Исключение преподавателей из подсчёта лиц требует передачи компьютеру
          образцов для локального сопоставления. Сверьте имя и ID с приложением
          Qorgau и разрешите доступ только нужному компьютеру. Автоматическое
          подключение само по себе не даёт этого разрешения.
        </p>
        <p className="fine">
          Проверка лица для разблокировки выполняется на сервере отдельно.
          Локальный экзамен и локальный пароль работают без этого разрешения.
        </p>
        {loading ? <p role="status">Загружаем компьютеры…</p> : devices.length === 0 ? (
          <p className="fine">Подключённых компьютеров пока нет.</p>
        ) : (
          <ul className="teacher-face-list teacher-face-access-list">
            {devices.map((device) => (
              <li key={device.id}>
                <div>
                  <strong>{device.name}</strong>
                  <small>ID: {device.id}</small>
                  <small>{device.enabled ? "Доступ разрешён" : "Ожидает разрешения преподавателя"}</small>
                  {device.enabled && !device.public_enrollment && (
                    <small>Компьютер подключён по коду или установщику преподавателя.</small>
                  )}
                </div>
                <button className="btn" disabled={busy} onClick={() => void setDeviceAccess(device)}>
                  {device.enabled ? "Отозвать разрешение" : "Разрешить этому компьютеру"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
