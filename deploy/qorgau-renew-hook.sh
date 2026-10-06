#!/bin/sh
set -eu

# The existing Caddy container mounts this named volume at /data.
# Never copy private keys into the webroot or a repository.
if [ "${RENEWED_LINEAGE:-}" != /etc/letsencrypt/live/qorgau-ip ]; then
    exit 0
fi
target=/var/lib/docker/volumes/app_caddy_data/_data/qorgau-tls
install -d -m 0700 "$target"
install -m 0644 "$RENEWED_LINEAGE/fullchain.pem" "$target/fullchain.pem.new"
install -m 0600 "$RENEWED_LINEAGE/privkey.pem" "$target/privkey.pem.new"
mv "$target/fullchain.pem.new" "$target/fullchain.pem"
mv "$target/privkey.pem.new" "$target/privkey.pem"
docker exec app-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --force
