#!/bin/bash
# Run as root with a deployment PUBLIC key file; never upload the private key.
set -euo pipefail
test "$(id -u)" = 0
test "$#" = 1
ssh-keygen -l -f "$1" >/dev/null
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
getent passwd qorgau-deploy >/dev/null || useradd --create-home --shell /bin/bash qorgau-deploy
install -d -m 0755 /usr/local/libexec /etc/systemd/system/qorgau.service.d
install -m 0755 "$source_dir/apply_release.py" /usr/local/sbin/qorgau-deploy
cat > /usr/local/libexec/qorgau-deploy-ssh <<'SH'
#!/bin/sh
exec /usr/bin/sudo -n /usr/local/sbin/qorgau-deploy "$SSH_ORIGINAL_COMMAND"
SH
chmod 0755 /usr/local/libexec/qorgau-deploy-ssh
printf '%s\n' 'qorgau-deploy ALL=(root) NOPASSWD: /usr/local/sbin/qorgau-deploy *' > /etc/sudoers.d/qorgau-deploy
chmod 0440 /etc/sudoers.d/qorgau-deploy
visudo -cf /etc/sudoers.d/qorgau-deploy
install -d -m 0755 -o root -g root /home/qorgau-deploy/.ssh
printf 'restrict,command="/usr/local/libexec/qorgau-deploy-ssh" %s\n' "$(cat "$1")" > /home/qorgau-deploy/.ssh/authorized_keys
chmod 0644 /home/qorgau-deploy/.ssh/authorized_keys
chown root:root /home/qorgau-deploy /home/qorgau-deploy/.ssh/authorized_keys
chmod 0755 /home/qorgau-deploy
# A release-local environment makes code and dependency rollback one atomic switch.
cat > /usr/local/libexec/qorgau-python <<'SH'
#!/bin/sh
python=/opt/qorgau/current/.venv/bin/python
# Historical manually deployed releases use the original shared environment.
test -x "$python" || python=/opt/qorgau/venv/bin/python
exec "$python" "$@"
SH
chmod 0755 /usr/local/libexec/qorgau-python
cat > /etc/systemd/system/qorgau.service.d/10-release-python.conf <<'UNIT'
[Service]
ExecStart=
ExecStart=/usr/local/libexec/qorgau-python -m uvicorn backend.proctor.app:app --host 172.19.0.1 --port 8765 --workers 1 --proxy-headers --forwarded-allow-ips 172.19.0.4 --no-access-log
UNIT
systemctl daemon-reload
echo 'Deployment receiver installed; the running application has not been restarted.'
