# Автоматическое развёртывание Qorgau

Каждый push в `main` запускает `.github/workflows/release.yml`: тесты Python,
проверку Ruff, сборку веб-интерфейса, затем `deploy`. Ветки и pull request
проходят проверки без доступа к production. Ручной запуск этого workflow на
`main` также собирает и проверяет Windows EXE, после чего обновляет его на сервере.
Обычный push сохраняет уже установленный EXE. Теги создают GitHub Release,
но не переключают production на старую версию.

Деплой передаёт проверенный код и собранный `web/dist` по SSH на
`212.19.134.23`. На сервере создаётся отдельный каталог выпуска, сохраняются
скачиваемые файлы, проверяются зависимости и запускается существующее резервное
копирование SQLite/видео. После этого атомарно переключается
`/opt/qorgau/current` и перезапускается только `qorgau.service`.

Проверка проходит через публичный HTTPS: `/api/health`, `/deployment.json`
(точный SHA коммита), Range-скачивание начала EXE. При ошибке возвращаются
предыдущие код и Python-окружение. База данных автоматически назад не
перезаписывается: несовместимые миграции требуют отдельного плана восстановления
из резервной копии. Данные, настройки, TLS и другие проекты на сервере не входят
в архив выпуска. Существующие выпуски и окружения сохраняются для отката;
при недостатке места новый деплой останавливается до переключения.

## Установленная конфигурация

- GitHub Actions secrets: `QORGAU_SSH_KEY`, `QORGAU_KNOWN_HOSTS`.
- SSH-пользователь: `qorgau-deploy`; ключ допускает только команду
  `deploy <commit SHA> <archive SHA-256>`, без shell, forwarding и PTY.
- Приёмник: `/usr/local/sbin/qorgau-deploy` — root-owned копия `apply_release.py`.
- Обёртка ключа: `/usr/local/libexec/qorgau-deploy-ssh`.
- Настройка systemd: `/etc/systemd/system/qorgau.service.d/10-release-python.conf`.
- Код/зависимости переключаются одной ссылкой; новые зависимости устанавливаются
  от непривилегированного пользователя, без запуска загруженного кода от root.
- Деплои сериализованы GitHub concurrency и серверным `flock`. Устаревший запуск
  пропускается, если его SHA уже не совпадает с `main`.

Для установки на этом же сервере с новым ключом загрузить содержимое `deploy/`
и публичный ключ, затем выполнить `sudo bash deploy/install-cicd.sh <public-key>`.
Приватный ключ хранится в GitHub secret; `QORGAU_KNOWN_HOSTS` содержит проверенный
host key сервера. Пароли и ключи в Git не добавлять. Изменение приёмника требует
повторной установки через административный SSH: сам архив его не обновляет.

## Проверка и откат

```sh
curl --fail https://212.19.134.23/api/health
curl --fail https://212.19.134.23/deployment.json
sudo systemctl status qorgau --no-pager
sudo journalctl -u qorgau -n 60 --no-pager
```

Статус и журнал автоматического запуска: GitHub → Actions →
**Verify and package Qorgau** → **deploy**. Повторный запуск jobs в Actions
повторяет деплой только пока этот commit является актуальным `main`.
Для ручного возврата выбрать сохранённый каталог выпуска:

```sh
sudo ln -s /opt/qorgau/releases/<нужный-выпуск> /opt/qorgau/rollback.next
sudo mv -Tf /opt/qorgau/rollback.next /opt/qorgau/current
sudo systemctl restart qorgau
curl --fail https://212.19.134.23/api/health
```
