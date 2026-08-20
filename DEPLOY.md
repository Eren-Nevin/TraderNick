# DEPLOY.md — deploying TraderNick on a fresh server

This is a complete, assume-nothing runbook for standing up TraderNick from
GitHub on a clean Linux server and exposing the dashboard + admin pages to the
internet through nginx (with HTTPS and HTTP basic auth). It is written to be
followed step by step by an operator or an agent. Commands assume Ubuntu/Debian;
adjust the package manager for other distros.

---

## 0. What you are deploying

TraderNick is a Docker Compose stack of ~30 containers on one host:

- **ClickHouse** — the single datastore (all services read/write it).
- **dashboard** — a SvelteKit (Node) web app. The only browser-facing service.
- **dashboard_backend** — the dashboard's read/analytics API (ClickHouse-backed).
  (Build context is still `./services/data_server`; the *service* was renamed for
  clarity.)
- **admin_server** — the admin/job-control API (HTTP basic-auth protected). The
  dashboard's admin pages call it; it fans out to the ingestion services.
- **data_provider** — an external client-library API (the `tradernick-data-provider`
  PyPI package talks to this). Not used by the dashboard.
- **`*_live` / `*_backfill`** ingestion workers (hyperliquid, binance, transfers,
  aave, uniswap, aerodrome, lido, morpho, spark, gmx, data_process), plus
  `tradernick_admin`, `notifications_monitor`, `notifications_bot` — all internal.

### Service → host-port map (after the localhost hardening in this repo)

| Service | Container port | Host binding | Internet-facing? |
|---|---|---|---|
| dashboard | 3000 | `127.0.0.1:10000` | **via nginx only** |
| admin_server | 8000 | `127.0.0.1:10001` | no (internal + local ops) |
| dashboard_backend | 8000 | `127.0.0.1:10002` | no |
| clickhouse (HTTP) | 8123 | `127.0.0.1:10003` | **never** |
| clickhouse (native) | 9000 | `127.0.0.1:10004` | **never** |
| data_provider | 8000 | `0.0.0.0:10005` | only if you want external clients (see §8.4) |
| all ingestion workers | 8000 | none | no |

**Security model:** everything except `data_provider` binds to `127.0.0.1`, so
the only way in from the internet is nginx (port 443). The SvelteKit app has **no
built-in login**, and its `/admin` pages drive privileged actions — so nginx adds
HTTP basic auth in front of the whole site. Never bind the dashboard to
`0.0.0.0`; that would let visitors reach `SERVER_IP:10000` directly and bypass
nginx auth.

### How the dashboard talks to its backends (why nginx is simple)

The browser only ever makes **same-origin** requests to the dashboard
(`/api/...`). SvelteKit server routes inside the dashboard container proxy those
to `dashboard_backend` and `admin_server` over the docker network. There are no
`PUBLIC_*`/build-time backend URLs. Result: **nginx proxies exactly one upstream**
(the dashboard); the admin UI is just a route within it.

---

## 1. Prerequisites

### Hardware (ClickHouse is the driver)
- **Light** (a few tokens, recent data only): 8 vCPU, 32 GB RAM, 200 GB SSD.
- **Full** (all families + history back to 2024, large backfills): 16+ vCPU,
  128 GB RAM, 2+ TB fast NVMe, plus swap. ClickHouse alone can hold >1 TB and use
  tens of GB RAM under heavy queries/backfills.
- A dedicated data disk is recommended (mounted anywhere; you point env vars at it).

### Software
```bash
# Docker Engine + Compose plugin (v2). On Ubuntu/Debian:
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # log out/in so `docker` works without sudo
docker version && docker compose version   # confirm compose v2 (the `docker compose` subcommand)

# nginx + certbot + htpasswd tool + git
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx apache2-utils git
```

### Network / DNS
- A domain name (e.g. `tradernick.example.com`) with an **A record pointing at the
  server's public IP**. Required for HTTPS via Let's Encrypt.
- Inbound firewall: allow **80** and **443** only (see §9). Do **not** open
  10000–10005.

### Credentials you must have
- A **DeFiStream API key** (`DEFISTREAM_API_KEY`) — ingestion cannot run without it.

---

## 2. Clone the repository

```bash
cd /opt   # or wherever you keep apps
git clone https://github.com/Eren-Nevin/TraderNick.git
cd TraderNick
```

---

## 3. Configure `.env`

`.env` is gitignored and holds all secrets + host-specific settings. Start from
the template and edit:

```bash
cp .env.example .env
```

`.env.example` documents **every** key. The ones you MUST review:

| Key | What to set |
|---|---|
| `DEFISTREAM_API_KEY` | your real DeFiStream key |
| `CLICKHOUSE_PASSWORD` | a strong password |
| `ADMIN_USER` / `ADMIN_PASSWORD` | admin-API credentials (used internally by the dashboard proxy) — change from defaults |
| `NOTIFICATIONS_ADMIN_SECRET` | random string (only matters if you use Telegram notifications) |
| `WEB_AUTH_USER` / `WEB_AUTH_PASSWORD` | **the nginx basic-auth login for the public site** (dashboard + admin). Change these. |
| `CLICKHOUSE_DATA_DIR` | host path for the ClickHouse data dir (e.g. `/mnt/data/tradernick/clickhouse`). Defaults to repo-local `./data/clickhouse`. |
| `SNAPSHOTS_HOST_DIR` | host path for data_provider snapshots (e.g. `/mnt/data/tradernick/snapshots`). Defaults to `./data/snapshots`. |
| `WALLETS_PARQUET_HOST` | host dir holding `wallets.parquet` (optional, for wallet labels). Defaults to `./data`. |
| `MAX_CONCURRENT_BACKFILLS` | per-family backfill concurrency (default 8). |

Ingestion scope (which data to collect) is driven by the `*_ENABLED` flags,
`*_CHAINS`, `EVM_ERC20_TRANSFERS`, `INGEST_TOKENS`, etc. — all documented inline
in `.env.example`. Trim these to only the domains/tokens you want; each enabled
family runs a `_live` and `_backfill` container.

> The internal `*_LIVE_URL`/`*_BACKFILL_URL` values are docker service-name DNS
> (e.g. `http://binance_live:8000`) — leave them unless you rename services.

---

## 4. Create the host data directories

Create whatever paths you set for `CLICKHOUSE_DATA_DIR` and `SNAPSHOTS_HOST_DIR`
(and `WALLETS_PARQUET_HOST` if using labels), and make them writable by your user:

```bash
# Example if you pointed the env vars at /mnt/data/tradernick:
sudo mkdir -p /mnt/data/tradernick/clickhouse /mnt/data/tradernick/snapshots
sudo chown -R "$(id -u):$(id -g)" /mnt/data/tradernick
# If you kept the ./data defaults instead:
mkdir -p ./data/clickhouse ./data/snapshots
```

The ClickHouse **schema is created automatically** on first start from
`clickhouse/init/01_schema.sql` + `02_materializer_locks.sql` — but ONLY when the
data dir is empty. (It never re-runs on an existing volume.)

---

## 5. Host-specific compose adjustment (CPU pinning)

`docker-compose.yml` pins ClickHouse to CPU cores 0–5 (`cpuset: "0-5"`), which
assumes **at least 6 cores**. On a smaller machine this will fail or starve CH.
Edit `docker-compose.yml`:

- `< 6 cores`: change `cpuset: "0-5"` under the `clickhouse:` service to your
  range (e.g. `"0-1"`) or delete the line to let it use all cores.
- The `*_live` services have a shared `cpus: '2.0'` limit (anchor `live_cpu_limit`);
  fine to leave, or lower on small hosts.

---

## 6. Build and start

```bash
docker compose up -d --build
```

First build pulls the ClickHouse image and builds the ingestion + dashboard +
data_server images (several minutes). ClickHouse starts first (others `depends_on`
it with a healthcheck), runs the schema init, then everything else comes up.

---

## 7. Verify

```bash
docker compose ps                 # all services "Up" (clickhouse "healthy")
docker compose logs -f clickhouse # watch schema init on first boot
# App reachable locally (before nginx):
curl -s http://127.0.0.1:10000/ | head        # dashboard (SvelteKit) responds
curl -s http://127.0.0.1:10002/tokens | head  # dashboard_backend API
# admin_server needs basic auth:
curl -s -u "$ADMIN_USER:$ADMIN_PASSWORD" http://127.0.0.1:10001/streams | head
```

If ClickHouse logs show it created the tables and the dashboard returns HTML,
the core stack is healthy.

---

## 8. First-run bootstrap (after the stack is up)

### 8.1 Wallet labels (optional but recommended)
Transfer/wallet views are unlabelled until you load a wallet catalogue. If you
have a `wallets.parquet`, place it in `WALLETS_PARQUET_HOST` (default `./data`)
and load it:

```bash
# Standalone (runs inside an ingestion-image container):
docker compose run --rm tradernick_admin python -m scripts.bootstrap_wallets --src /app/data/wallets.parquet
# ...or via the admin API (multipart upload):
curl -u "$ADMIN_USER:$ADMIN_PASSWORD" -F file=@wallets.parquet http://127.0.0.1:10001/admin/wallets
```
Skip this if you don't have a catalogue — everything else works, labels are just
blank.

### 8.2 Token batches / overrides / notification rules
These are runtime config managed in the **admin UI** (`/admin` → Token Batches,
etc.) once the site is up — not required for boot. Seed defaults come from
`INGEST_TOKENS` / `INGEST_TOKENS_BATCH_2` / `INGEST_NAMED_BATCHES` in `.env`.

### 8.3 Historical backfills
Live ingestion starts automatically. Historical backfills are triggered from the
admin UI (Jobs) or the admin API. They run in the `*_backfill` containers, capped
per family by `MAX_CONCURRENT_BACKFILLS`.

### 8.4 data_provider (external client API)
Port `10005` stays bound to `0.0.0.0` so external Python clients (the
`tradernick-data-provider` package) can reach it. If you have **no** external
clients, bind it to localhost too: in `docker-compose.yml` change the
`data_provider` port to `"127.0.0.1:10005:8000"`. If you DO expose it, put it
behind nginx/TLS + auth as well, or firewall it to known client IPs.

---

## 9. nginx reverse proxy (public HTTPS + basic auth)

The goal: `https://tradernick.example.com` → the dashboard (which serves the app,
the admin pages, and all `/api/*` same-origin). nginx adds HTTP basic auth in
front of everything, using the `WEB_AUTH_USER` / `WEB_AUTH_PASSWORD` from `.env`.

### 9.1 Generate the basic-auth file from `.env`

```bash
cd /opt/TraderNick
# read the two vars out of .env and write /etc/nginx/.htpasswd
set -a; . ./.env; set +a
sudo htpasswd -bc /etc/nginx/.htpasswd "$WEB_AUTH_USER" "$WEB_AUTH_PASSWORD"
sudo chmod 640 /etc/nginx/.htpasswd
sudo chown root:www-data /etc/nginx/.htpasswd
```
Re-run this (without `-c`) whenever you change the credentials in `.env`.

### 9.2 nginx site config

Create `/etc/nginx/sites-available/tradernick`:

```nginx
server {
    listen 80;
    server_name tradernick.example.com;
    # certbot fills in the 80→443 redirect; keep this until then.
}

server {
    listen 443 ssl http2;
    server_name tradernick.example.com;

    # --- TLS (paths filled in by certbot in §9.3) ---
    # ssl_certificate     /etc/letsencrypt/live/tradernick.example.com/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/tradernick.example.com/privkey.pem;

    # --- HTTP basic auth over the WHOLE site (dashboard + admin) ---
    auth_basic           "TraderNick";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # heavy chart/backfill queries can run for minutes; allow big uploads for
    # the wallet-parquet admin path.
    proxy_read_timeout   300s;
    proxy_send_timeout   300s;
    client_max_body_size 200m;

    # security headers
    add_header X-Frame-Options SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;

    location / {
        proxy_pass http://127.0.0.1:10000;      # the dashboard (SvelteKit)
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # websocket/upgrade support for SvelteKit
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

> One credential covers the whole site. If you want a **separate, stronger** login
> just for `/admin` and `/api/admin`, add a second `location` block for each with
> its own `auth_basic_user_file` (generate a second htpasswd the same way). The
> admin API is additionally protected by `ADMIN_USER`/`ADMIN_PASSWORD` inside the
> stack, but that credential is injected server-side — it does **not** gate the
> browser, which is why the nginx layer is mandatory.

Enable it and test:
```bash
sudo ln -s /etc/nginx/sites-available/tradernick /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

### 9.3 TLS certificate (Let's Encrypt)

```bash
sudo certbot --nginx -d tradernick.example.com
```
certbot obtains the cert, uncomments/injects the `ssl_certificate*` lines, adds
the 80→443 redirect, and sets up auto-renewal. Re-test: `sudo nginx -t && sudo
systemctl reload nginx`.

Visit `https://tradernick.example.com` — the browser prompts for the basic-auth
login (`WEB_AUTH_USER`/`WEB_AUTH_PASSWORD`), then the dashboard loads. `/admin`
works the same, same origin.

---

## 10. Firewall

Expose only 80/443. ClickHouse and the internal APIs are already localhost-bound,
but a firewall is defense-in-depth:
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'   # 80 + 443
sudo ufw enable
sudo ufw status
```

---

## 11. Updating / redeploying

```bash
cd /opt/TraderNick
git pull
docker compose up -d --build        # rebuilds changed images, recreates changed containers
# ClickHouse is not recreated unless its config changed; data persists in
# CLICKHOUSE_DATA_DIR.
```
To apply only ingestion code changes without touching ClickHouse/live services,
target the changed services, e.g. `docker compose up -d --build --no-deps
binance_backfill`.

---

## 12. Troubleshooting

- **Dashboard 502 / "Cannot connect to host clickhouse:8123":** ClickHouse isn't
  healthy yet. `docker compose ps`; wait for `clickhouse (healthy)`; check
  `docker compose logs clickhouse`.
- **Schema didn't load:** it only runs on an **empty** `CLICKHOUSE_DATA_DIR`. If
  the dir had leftover files, CH skips init. Start from an empty dir, or apply
  `clickhouse/init/01_schema.sql` manually via `clickhouse-client`.
- **`docker compose up` mounts an empty `./data/clickhouse`:** you didn't set
  `CLICKHOUSE_DATA_DIR` in `.env` and the default kicked in. Set it and re-`up`.
- **nginx basic-auth prompt loops / 401:** regenerate `/etc/nginx/.htpasswd` from
  `.env` (§9.1); confirm `auth_basic_user_file` path.
- **504 on heavy charts:** raise `proxy_read_timeout` (dashboard_backend's own
  response timeout is ~240s).
- **Ingestion idle / no data:** confirm `DEFISTREAM_API_KEY` is set and the
  relevant `*_ENABLED` flags are on; `docker compose logs <family>_live`.
- **`cpuset` error on start:** your host has fewer cores than the pin in §5.

---

## 13. Security checklist (do not skip)

- [ ] Changed `WEB_AUTH_PASSWORD`, `ADMIN_PASSWORD`, `CLICKHOUSE_PASSWORD` from
      the `change_me`/default values.
- [ ] `/etc/nginx/.htpasswd` generated; `auth_basic` active on the site.
- [ ] HTTPS via certbot; HTTP redirects to HTTPS.
- [ ] Firewall allows only 22/80/443.
- [ ] ClickHouse + admin_server + dashboard_backend + dashboard bound to
      `127.0.0.1` (they are, in this repo's compose).
- [ ] Decided on `data_provider` (10005): localhost-only, or firewalled/behind
      nginx if external clients need it.
