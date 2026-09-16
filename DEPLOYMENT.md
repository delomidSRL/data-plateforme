# Deploying Data Plateforme with Docker

This guide covers deploying the control plane (backend + frontend + control-plane
PostgreSQL) to a VM using Docker Compose, and how to publish new image versions to
Docker Hub. It does **not** cover Airflow/MinIO/Superset/tenant Postgres — those are
provisioned per-tenant by the platform itself onto managed servers (see
`backend/app/templates/`).

There is no TLS in front of this setup yet — put it behind a reverse proxy
(nginx/Caddy/Traefik) or a load balancer with TLS termination if it is reachable
from outside a trusted network.

## Architecture

Three containers, one Docker network, one named volume:

| Service    | Image                                  | Port (host) | Role                                  |
|------------|-----------------------------------------|--------------|----------------------------------------|
| `postgres` | `postgres:16`                           | not exposed  | Control-plane database                 |
| `backend`  | `delomidit/dataplateforme-backend`    | `8000`       | FastAPI API                            |
| `frontend` | `delomidit/dataplateforme-frontend`   | `80`         | React app served by nginx              |

The frontend image is built once and configured at **container start**, not at build
time: an nginx entrypoint script regenerates `config.js` from the `API_BASE_URL`
environment variable on every start, so the same image can be deployed to any host
without a rebuild.

Compose project name is pinned explicitly (`name: dataplateforme-prod` in
`docker-compose.prod.yml`) so it can never collide with the local dev compose file's
services if both ever end up running on the same machine.

## 1. Build and publish images (from your dev machine)

Requires a Docker Hub account and `docker login` already done locally.

```bash
# backend
cd backend
docker build -t delomidit/dataplateforme-backend:<version> -t delomidit/dataplateforme-backend:latest .
docker push delomidit/dataplateforme-backend:<version>
docker push delomidit/dataplateforme-backend:latest

# frontend
cd ../frontend
docker build -t delomidit/dataplateforme-frontend:<version> -t delomidit/dataplateforme-frontend:latest .
docker push delomidit/dataplateforme-frontend:<version>
docker push delomidit/dataplateforme-frontend:latest
```

Use semantic versioning (`1.0.0`, `1.1.0`, ...) for `<version>`. Only rebuild and push
the image(s) whose source actually changed — backend and frontend version
independently.

## 2. VM prerequisites

- Docker Engine + the Compose plugin installed (`docker compose version` works)
- The VM's IP or hostname reachable from wherever the browser will be used
- Ports `80` (or whatever `FRONTEND_PORT` you choose) and `8000` (or
  `BACKEND_PORT`) open on the VM's firewall

## 3. First-time setup on the VM

Copy `docker-compose.prod.yml` and `.env.prod.example` to a directory on the VM
(e.g. `/opt/dataplateforme/`):

```bash
mkdir -p /opt/dataplateforme && cd /opt/dataplateforme
# copy docker-compose.prod.yml and .env.prod.example here, then:
cp .env.prod.example .env
nano .env   # fill in every value, see the reference table below
```

**`.env` holds real secrets — never commit it, and never edit `.env.prod.example`
in place with real values.** Keep `.env.prod.example` as a template with
placeholders only; a `.env.example`-style file is meant to be shared/version
controlled, `.env` is not.

Then pull and start:

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

On first start, the backend entrypoint runs `alembic upgrade head` then seeds the
bootstrap admin account (`ADMIN_EMAIL` / `ADMIN_PASSWORD` from `.env`) before
starting uvicorn. This seed step is idempotent — safe to run again on every restart.

Verify:

```bash
docker compose -f docker-compose.prod.yml ps        # all 3 healthy
curl http://<vm-host>:8000/api/health               # backend
curl -I http://<vm-host>/                           # frontend
```

Then open `http://<vm-host>/` in a browser and log in with the admin credentials.

## 4. Environment variables reference (`.env`)

### Images

| Variable | Example | Notes |
|---|---|---|
| `DOCKERHUB_NAMESPACE` | `delomidit` | Docker Hub namespace images are published under |
| `BACKEND_IMAGE_TAG` | `1.2.0` | Backend image tag to deploy |
| `FRONTEND_IMAGE_TAG` | `1.2.0` | Frontend image tag to deploy |

### Ports published on the VM

| Variable | Default | Notes |
|---|---|---|
| `BACKEND_PORT` | `8000` | Host port the API is published on |
| `FRONTEND_PORT` | `80` | Host port the web app is published on |

### Public URLs

| Variable | Example | Notes |
|---|---|---|
| `FRONTEND_URL` | `http://203.0.113.10` | The URL a browser uses to reach the frontend. Used verbatim as the backend's `CORS_ORIGINS`. |
| `BACKEND_PUBLIC_URL` | `http://203.0.113.10:8000` | The URL a *browser* uses to reach the backend (not the internal Docker service name) — baked into the frontend's runtime config on container start. |

> **CORS pitfall**: a browser's `Origin` header never includes a default port
> (`:80` for http, `:443` for https). If `FRONTEND_PORT` is `80` or `443`, do
> **not** append `:80`/`:443` to `FRONTEND_URL`, or every request gets
> CORS-blocked because the exact-match comparison fails.

### Control-plane database

| Variable | Example | Notes |
|---|---|---|
| `POSTGRES_USER` | `dataplateforme` | |
| `POSTGRES_PASSWORD` | *(generate one)* | Required, no default |
| `POSTGRES_DB` | `dataplateforme` | |

### Secrets

| Variable | Example | Notes |
|---|---|---|
| `APP_SECRET_KEY` | *(see below)* | Fernet key used to encrypt/decrypt stored credentials (data source passwords, SSH keys, etc.) |
| `JWT_SECRET_KEY` | *(any long random string)* | Signs session JWTs |

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Use a **different** `APP_SECRET_KEY` per environment. If you need to move data
between environments (e.g. exporting sources/projects from dev to this VM), the
stored secrets must be decrypted with the source environment's key and
re-encrypted with the target's — never reuse the same key across environments and
never copy encrypted values as-is between them.

Optional, not in `.env.prod.example` (defaults shown, override only if needed):

| Variable | Default | Notes |
|---|---|---|
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `480` (8h) | JWT/session lifetime. The frontend separately shows an inactivity warning after 30 minutes idle with a 20s countdown before logging out client-side — this variable is the hard upper bound on the token itself, independent of that. |
| `RESET_TOKEN_EXPIRE_MINUTES` | `30` | Password reset link lifetime |

### SMTP (password reset, invitations, quality alert notifications)

| Variable | Example |
|---|---|
| `SMTP_HOST` | `smtp.mail.ovh.net` |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | *(mailbox address)* |
| `SMTP_PASSWORD` | *(mailbox password)* |
| `SMTP_FROM` | *(sender address)* |
| `SMTP_TLS` | `true` |

### Bootstrap admin

Created once, idempotently, on first start.

| Variable | Notes |
|---|---|
| `ADMIN_NAME` | Display name |
| `ADMIN_EMAIL` | Login email |
| `ADMIN_PASSWORD` | Login password — change the default before going live |

## 5. Updating to a new version

```bash
cd /opt/dataplateforme
nano .env   # bump BACKEND_IMAGE_TAG and/or FRONTEND_IMAGE_TAG
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

`up -d` only recreates containers whose image actually changed — updating just the
frontend tag, for example, does not restart `postgres` or `backend`.

## 6. Connecting to the control-plane database directly (optional)

The `postgres` service is not published on the host, only reachable on the
internal Docker network. To connect from your local machine (e.g. with pgAdmin),
open an SSH tunnel to the container's bridge-network IP, run **from your local
machine** (not on the VM):

```bash
ssh -L 5433:<postgres-container-ip>:5432 <user>@<vm-host>
```

Then point pgAdmin at `localhost:5433` with the credentials from `.env`. Find the
container's internal IP with:

```bash
docker inspect dataplateforme-postgres --format '{{.NetworkSettings.Networks.dataplateforme_dataplateforme.IPAddress}}'
```

## 7. Locating an existing deployment

If you've lost track of where `.env`/`docker-compose.prod.yml` live on a VM you
already deployed to, Docker keeps the exact working directory and compose file
path in the running containers' labels — no need to search blindly:

```bash
docker inspect dataplateforme-backend --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
docker compose ls   # CONFIG FILES column, project "dataplateforme-prod"
```
