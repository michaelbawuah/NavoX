# NavoX public VPS deployment

The production overlay keeps PostgreSQL and Temporal private, binds application
HTTP only to loopback, and runs Caddy as the host HTTPS service. The assistant
routes stay in Next.js; other /api/v1 routes go to the Python API.

## Configuration

- Use a checkout of the green main commit. Record its full SHA before deployment.
- Store runtime secrets in the checkout's untracked .env, mode 0600, in a
  directory accessible only to the deployment operator.
- Preserve the reviewed provider policy and exact principal scopes. Copy only
  credentials needed by enabled routes. Never install an unqualified fallback.
- Generate separate random URL-safe NAVOX_DATABASE_PASSWORD and
  TEMPORAL_DATABASE_PASSWORD values (32 random bytes encoded as hexadecimal).
- Set NAVOX_PUBLIC_ORIGIN=https://navox.net and
  NAVOX_PUBLIC_API_BASE_URL=https://navox.net/api/v1.
- Set APP_ENVIRONMENT=production and WEB_ORIGIN=https://navox.net.
- Set Google and Canvas callback URIs to the corresponding HTTPS /api/v1 paths;
  register those exact callbacks with the approved OAuth clients.
- Preserve token-encryption keys when restoring encrypted connected credentials.
- Point the domain A record to the VPS. Remove obsolete parking AAAA records;
  set www to a CNAME for navox.net. Preserve existing email DNS records.
- Permit inbound SSH, HTTP and HTTPS. Do not publish database or workflow ports.

## Deploy

Install Docker Engine/Compose and Caddy from their official signed package
repositories. Use the overlay for every production Compose command:

```sh
docker compose -f docker-compose.yml -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.production.yml build
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d
caddy validate --config deploy/Caddyfile
install -m 0644 deploy/Caddyfile /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy
```

Compose applies the Python migrations before API/worker startup and the Node
assistant migrations before web/goals-worker startup. The Temporal UI is absent
from the default deployment; the operations profile binds it only to loopback.

## Existing data, backups and verification

For a cutover, preserve a private encrypted off-host database backup and stop
source writers/workers before the final consistent export. Preserve account,
workspace, provider qualification, approval and action audit records. Transfer
the matching Temporal history if resuming existing workflows. Restore and
verify the backup before migrations; never discard the original volume.

Verify API live/ready, HTTPS and secure session cookies, normal owner login,
saved assistant sessions, and active foundation/goals worker pollers. Compare
the deployed image/source SHA to main. Check that PostgreSQL, Temporal and the
Temporal UI are unreachable publicly. A failed migration blocks application
startup. Do not retry uncertain actions or repeat a successful Gmail test send.

Rollback uses the previous verified source/image and matching configuration.
Keep additive migrations in place unless their documented downgrade conditions
are satisfied. Do not start both old and new execution workers against copied
workflow history.

Public owner testing begins at https://navox.net/navox after health checks pass.
Mic permission is required for Talk; Hey NavoX additionally requires supported
on-device speech recognition and its local language pack.
