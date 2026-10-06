"""Student-facing interpretation of agent state; never changes exam access."""

REASONS = {
    "GAZE_DOWN": "Три длительных отвлечения вниз.",
    "GAZE_LEFT": "Три длительных отвлечения влево.",
    "GAZE_RIGHT": "Три длительных отвлечения вправо.",
    "PHONE_DETECTED": "Камера обнаружила телефон.",
    "TEACHER_LOCK": "Преподаватель приостановил контроль.",
    "CAMERA_UNAVAILABLE": "Не удалось получить или сохранить видео камеры.",
    "AGENT_RESTARTED": "Приложение было перезапущено во время контроля.",
    "AGENT_FAILURE": "Агент остановился из-за технической ошибки.",
    "TARGET_CLOSED": "Главное окно теста закрыто или заменено.",
    "GUARD_UNAVAILABLE": "Защита рабочего окружения недоступна.",
    "ENVIRONMENT_ATTEMPT": "Попытка выйти из разрешённого окна или использовать запрещённую клавишу.",
    "SERVER_UNAVAILABLE": "Связь с сервером отсутствует более 10 секунд.",
    "REMOTE_SESSION": "Обнаружен сеанс удалённого рабочего стола.",
    "DISPLAY_CHANGED": "Изменилось количество подключённых экранов.",
    "BROWSER_ATTEMPT": "Открыт сайт за пределами разрешённого адреса.",
    "CAMERA_FROZEN": "Изображение камеры не меняется более 5 секунд.",
}


def present(snapshot):
    state = snapshot["state"]
    lifecycle = state["lifecycle"]
    locked = state["access"] == "LOCKED"
    if locked:
        title = "Позовите преподавателя"
        message = (
            REASONS.get(state.get("reason"), "Требуется проверка события.")
            + " Обратитесь к преподавателю. Возврат взгляда или перезапуск приложения не снимает это состояние."
        )
        tone = "red"
        badge = "Контроль приостановлен"
    elif lifecycle == "COMPLETED":
        title = "Контроль завершён"
        message = "Преподаватель завершил сеанс. Убедитесь, что ответы отправлены в самой системе тестирования."
        tone = "green"
        badge = "Сеанс завершён"
    elif lifecycle == "RUNNING":
        title = "Тест проходит в вашей системе"
        message = (
            "Работайте в выбранном преподавателем браузере или приложении. Qorgau отслеживает события в фоне."
            if snapshot.get("camera")
            else "Связь с преподавателем работает. Камера не подготовлена: анализ взгляда, телефона и лиц сейчас не выполняется."
        )
        tone = "green" if snapshot.get("camera") else "amber"
        badge = "Идёт контроль" if snapshot.get("camera") else "Сеанс без камеры"
    elif snapshot.get("exam_id"):
        title = "Сеанс назначен. Ждём начала"
        message = "Подготовьте камеру и дождитесь команды преподавателя. Выбранная среда теста откроется автоматически."
        tone = "green"
        badge = "Ожидание старта"
    else:
        title = "Компьютер подключён"
        message = "Преподаватель увидит этот компьютер в своей панели, выберет среду теста и начнёт сеанс."
        tone = "neutral"
        badge = "Ожидание сеанса"
    return {
        "title": title,
        "message": message,
        "tone": tone,
        "badge": badge,
        "can_open": lifecycle == "RUNNING" and not locked,
        "can_calibrate": lifecycle != "RUNNING"
        and not snapshot.get("camera_preparing", False),
        "locked": locked,
        "complete": lifecycle == "COMPLETED",
    }
