# Deployment

The deployed shape is one small VPS running the whole stack under Docker
Compose, with Cloudflare in front of it. Everything is served from a single
origin, and the host publishes no ports at all.

```
yourdomain.com
      │  HTTPS, terminated by Cloudflare
      ▼
 Cloudflare edge
      │  the tunnel dials OUT from the box; nothing dials in
      ▼
 ┌─ VPS ─────────────────────────────────────────────┐
 │  cloudflared ──▶ web (Caddy)                       │
 │                    ├── /        built dashboard    │
 │                    └── /api/*   ──▶ api            │
 │                                     ├── db         │
 │                                     └── worker     │
 └────────────────────────────────────────────────────┘
```

## Why this shape

**One origin.** Caddy serves the dashboard and proxies the API under the same
hostname, so the session cookie works as written — `SameSite=Lax`, no CORS, no
`Access-Control-Allow-Credentials`. Splitting the dashboard and API across two
hostnames would force `SameSite=None` and a credentialed CORS layer, which is
where this class of bug lives. The live-timing WebSocket is same-origin for the
same reason and needs no special handling.

**No inbound ports.** `cloudflared` makes an outbound connection to Cloudflare,
so the firewall can deny everything except SSH. There are no certificates to
obtain or renew on the box; Cloudflare terminates TLS.

**No secret has a default.** The compose file refuses to start when one is
missing, rather than falling back to a development credential and looking
healthy while being open.

## Sizing

Measured on a running stack, not estimated:

| | Idle | Peak |
|---|---|---|
| api | 178 MiB | — |
| worker | 126 MiB | ~300 MiB loading telemetry |
| postgres | 62 MiB | — |

Disk: the FastF1 cache reached 456 MB for one season, and PostgreSQL about
100 MB per season, so allow a few GB for 2018–2026 plus whatever telemetry you
request.

**2 GB RAM is the floor.** A 1 GB instance will fight the OS when pandas loads
a session with telemetry. 55 GB of disk is comfortable.

## First deployment

### 1. The box

Any small VPS. Install Docker and the compose plugin, then:

```bash
git clone <your-repo> formula1-dashboard
cd formula1-dashboard
```

Deny inbound traffic except SSH. Nothing else needs to reach this host:

```bash
sudo ufw default deny incoming
sudo ufw allow OpenSSH
sudo ufw enable
```

### 2. The tunnel

In the Cloudflare dashboard, under **Zero Trust → Networks → Tunnels**, create
a tunnel, then add a **public hostname** pointing at your domain and routing to:

```
http://web:8080
```

`web` is the compose service name, which `cloudflared` resolves on the compose
network. Copy the tunnel token.

Your domain's nameservers must be Cloudflare's. The registrar does not matter.

### 3. Secrets

```bash
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
```

Fill in all four values. Generate the password hash on your own machine — it
never needs to exist in the clear on the server:

```bash
cd backend && uv run python -m scripts.hash_password
```

The hash is safe to paste into an env file: its fields are separated with `.`
rather than the conventional `$`, which Compose would otherwise interpolate.

### 4. Start

```bash
docker compose -f deploy/compose.prod.yaml --env-file deploy/.env up -d --build
```

The `migrate` service runs `alembic upgrade head` and must finish before `api`
and `worker` start. Check it landed:

```bash
docker compose -f deploy/compose.prod.yaml --env-file deploy/.env ps
docker compose -f deploy/compose.prod.yaml --env-file deploy/.env logs migrate
```

Then open your domain and sign in.

## Updating

```bash
git pull
docker compose -f deploy/compose.prod.yaml --env-file deploy/.env up -d --build
```

The code is baked into the image, so a deploy is a rebuild — there is no
mounted worktree and no reloader. Migrations run automatically before the API
starts.

## What is different from running locally

| | Local | Deployed |
|---|---|---|
| Sign-in | off (`DASHBOARD_AUTH_REQUIRED=false`) | required |
| Frontend | Vite dev server | built assets served by Caddy |
| Code | bind-mounted, `--reload` | baked into the image |
| Postgres | published on loopback | not published at all |
| Containers | non-root | non-root |
| F1 TV sign-in | one click via the companion extension | manual cookie paste |

That last row is not a configuration choice. The FastF1 companion extension
posts the token to `http://localhost:8000/auth` **on the machine running the
browser**, which is not the server, so the round trip cannot complete. The
deployed instance does not mount that route and withholds the one-click link
rather than offering a sign-in that goes nowhere. Use the collapsed manual
paste in the live view instead.

## Upgrading an installation that predates these changes

Both are one-time and neither touches data:

```bash
# The unprivileged container user cannot write volumes created under root.
docker compose down
./scripts/prepare-existing-volumes.sh

# POSTGRES_PASSWORD only applies when a cluster is first created, so a volume
# initialised with trust keeps accepting unauthenticated connections.
docker compose up -d db
POSTGRES_PASSWORD=... ./scripts/secure-existing-postgres.sh
```

Both scripts are idempotent, report what they change, and verify the result.

## Backups

`scripts/backup-database.sh` takes a compressed, restorable dump, verifies it
can be read back before keeping it, and prunes to the most recent `BACKUP_KEEP`
(14 by default). Schedule it on the host:

```cron
# 03:15 UTC daily
15 3 * * * cd /path/to/formula1-dashboard && \
  COMPOSE_FILE=deploy/compose.prod.yaml POSTGRES_PASSWORD=... \
  ./scripts/backup-database.sh >> /var/log/f1-backup.log 2>&1
```

**A dump on the same machine is not an off-site backup.** Losing the VPS loses
both. Set `BACKUP_UPLOAD_COMMAND` to copy each dump somewhere else; `{}` is
replaced with the path, and a failed upload fails the run rather than passing
quietly:

```bash
BACKUP_UPLOAD_COMMAND='rclone copy {} r2:f1-backups/'
```

### Rehearse the restore

A backup nobody has restored is a hypothesis. `scripts/restore-database.sh`
restores into a scratch database by default, leaving the live one alone:

```bash
POSTGRES_PASSWORD=... RESTORE_DB=restore_check \
  ./scripts/restore-database.sh backups/formula1-dashboard-<stamp>.dump
```

It prints the row counts that landed, so you can compare them with the live
database. Restoring over the live database requires typing its name, or
`ASSUME_YES=1`.

Do this after the first deployment and then periodically. The archive is
re-ingestible from FastF1 if it comes to it, but slowly and against the
request budget.

## Still outstanding

Deliberately kept out of the deployment work:

- **Error reporting.** No Sentry or equivalent; failures are visible only in
  `docker compose logs`.
- **Rate limiting beyond sign-in.** Only the login endpoint is throttled.
