# Deployment and operations

Everything needed to run this, connect it to Gnani, and diagnose it when it does
not behave.

- [What runs where](#what-runs-where)
- [Running it](#running-it)
- [Environment variables](#environment-variables)
- [Connecting to the real Gnani platform](#connecting-to-the-real-gnani-platform)
- [Verifying a deployment](#verifying-a-deployment)
- [Troubleshooting](#troubleshooting)
- [Data management](#data-management)
- [Deploying beyond a laptop](#deploying-beyond-a-laptop)

---

## What runs where

`docker compose --profile mock up` starts three containers, grouped in Docker
Desktop under the project name **`emi-voice-agent`**.

| Service | Container | Image | Port | Purpose |
|---|---|---|---|---|
| `api` | `emi-voice-agent-api` | `emi-voice-agent/api:1.0.0` | **8000** | The application |
| `mongo` | `emi-voice-agent-mongo` | `mongo:7` | **27017** | Storage |
| `mock-gnani` | `emi-voice-agent-mock-console` | `emi-voice-agent/mock-console:1.0.0` | **9100** | Stand-in Agents Console (profile `mock`) |

Volumes: `emi-voice-agent_mongo-data` (database) and `emi-voice-agent_api-data`
(only used by the JSON fallback backend).

### URLs

| URL | What |
|---|---|
| http://localhost:8000/ | Dashboard |
| http://localhost:8000/docs | Swagger |
| http://localhost:8000/redoc | ReDoc |
| http://localhost:8000/openapi.json | OpenAPI spec |
| http://localhost:8000/health | Liveness |
| http://localhost:8000/ready | Readiness — reports which storage backend is live |
| ws://localhost:8000/ws/calls | Dashboard live updates |
| http://localhost:9100/health | Mock console, lists available scenarios |

The API image runs as a non-root user and carries a `curl`-based healthcheck;
compose gates `api` on Mongo's healthcheck so it never starts against a cold
database.

---

## Running it

### With Docker (recommended)

```bash
cp .env.example .env          # then set WEBHOOK_API_KEY
docker compose --profile mock up --build
```

Drop `--profile mock` for `api` + `mongo` only — do that when pointing at the
real Gnani platform.

> **Windows:** if `docker` is not recognised, open a new terminal (Docker Desktop
> adds itself to `PATH` on install, so an older shell will not have it), or
> prepend it:
> `$env:Path = "C:\Program Files\Docker\Docker\resources\bin;$env:Path"`

### Without Docker

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
```

Two terminals:

```bash
uvicorn app.main:app --port 8000

WEBHOOK_API_KEY=<same as .env> APP_BASE_URL=http://127.0.0.1:8000 \
  uvicorn tests.mock_gnani.main:app --port 9100
```

Set `STORAGE_BACKEND=json` to run with no database at all — the whole call
lifecycle works, which is why the fallback exists.

### Populate the dashboard without telephony

```bash
python -m scripts.seed_scenarios      # 10 conversation outcomes + 3 failure paths
```

Everything goes through the real code path — `POST /api/Initial_Message`, the
greeting callback, then the webhook. Nothing is written to storage directly.

---

## Environment variables

`.env.example` documents the full set. The ones that actually matter:

| Variable | Notes |
|---|---|
| `WEBHOOK_API_KEY` | Shared secret Gnani must send as `X-Webhook-Key`. **Auth fails closed if unset** — the endpoint returns 500 rather than accepting anything. |
| `TIMEZONE` | **Must match the agent's Time Zone in the console.** "today" and "tomorrow" are resolved against it, so a mismatch puts every PTP date off by a day. |
| `GNANI_AGENT_ID` | The console calls this both `agentId` and `bot_id`. |
| `GNANI_BASE_URL` | `https://api.inya.ai` for the real platform. |
| `STORAGE_BACKEND` | `mongo` or `json`. |
| `CURRENCY` | Mapped to a spoken word before reaching TTS — never send the ISO code. |
| `ADOPT_CONSOLE_CALLS` | Record webhooks for calls the console started. Default true. |

### Two variables that exist only for Docker

`docker-compose.yml` loads `.env` into the container **and** uses it for variable
substitution. So `${STORAGE_BACKEND}` in compose resolves from `.env` — meaning a
value meant for a local run silently becomes the container's value too. Both of
these caused real, hard-to-spot failures during development:

| Docker variable | Default | Why it is separate |
|---|---|---|
| `STORAGE_BACKEND_DOCKER` | `mongo` | A local `.env` set to `json` otherwise made the container run on the file backend while MongoDB sat idle and empty. |
| `GNANI_BASE_URL_DOCKER` | `http://mock-gnani:9100` | `.env` holds `127.0.0.1:9100` for host runs; inside a container that address is the container itself, so the API dialled itself and every trigger failed with `ConnectError`. |

To point the containerised app at the real platform:

```bash
GNANI_BASE_URL_DOCKER=https://api.inya.ai docker compose up -d
```

---

## Connecting to the real Gnani platform

Gnani cannot reach `localhost`, so the app must be publicly reachable.

### 1. Start a tunnel

`cloudflared` needs no account:

```powershell
# Windows, no winget required
$dir = "$env:LOCALAPPDATA\cloudflared"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Invoke-WebRequest `
  -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
  -OutFile "$dir\cloudflared.exe" -UseBasicParsing

& "$dir\cloudflared.exe" tunnel --url http://localhost:8000
```

It prints a `https://<random-words>.trycloudflare.com` URL. `ngrok http 8000`
works equally well.

**Verify before configuring anything:** open `https://<tunnel>/health` in a
browser. If that does not return JSON, nothing downstream will work.

> A quick tunnel's URL changes every time the process restarts, and dies with it.
> Keep it running, and re-point the console if you restart it.

### 2. Configure the console

**Configuration → Analytics → Post-Call Trigger → Configure:**

| Field | Value |
|---|---|
| Method | `POST` |
| URL | `https://<tunnel>/hook` |
| Headers | **on** |
| Key | `X-Webhook-Key` |
| Value | your `WEBHOOK_API_KEY` |

Save the dialog, then **save the agent**.

> **Use `/hook`, not `/api/v1/webhooks/post-call`.** The console rejects long
> URLs with "enter a valid URL" and gives no other clue. `/hook` is an alias for
> the same handler, with the same authentication, and exists purely to stay under
> that limit.

**Dynamic Messages** (Conversation Flow tab) is optional. Only enable it when
calls are started through `/api/Initial_Message` and you want the personalised
greeting; leave it off for console tests, where it would replace the configured
Greeting Message with a generic one.

### 3. Make a call

Any call from the console — `Web-based (Voice)` or `Trigger Agent Call` —
produces a post-call webhook. Calls not started via `/api/Initial_Message` are
recorded with `origin: "gnani_console"` and no customer fields, because the
platform does not send them.

Watch it arrive:

```bash
docker compose logs api -f
```

Look for `webhook.adopted_console_call` then `webhook.processed`. Allow 1–2
minutes — analytics generation is not instant.

---

## Verifying a deployment

```bash
curl http://localhost:8000/health     # {"status":"ok",...}
curl http://localhost:8000/ready      # confirms the live storage backend
pytest -q                             # 135 tests
python -m tests.test_disposition_accuracy
```

`/ready` reporting `"storage":"mongo"` is the check that matters — it is the
difference between running on MongoDB and silently running on the file fallback.

Indexes, after the first start:

```bash
docker compose exec mongo mongosh emi_voice_agent --quiet \
  --eval "db.webhook_events.getIndexes()"
```

`event_id` must show `unique: true`. That index, not application logic, is what
enforces webhook idempotency.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/ready` says `"storage":"json"` under Docker | `.env` shadowing | Use `STORAGE_BACKEND_DOCKER`, not `STORAGE_BACKEND` |
| Every trigger fails with `ConnectError` | Container dialling itself | Set `GNANI_BASE_URL_DOCKER` |
| Console: "enter a valid URL" | URL length limit | Use `/hook` |
| Webhook returns `401` | Header missing or wrong | Reopen the Post-Call Trigger dialog; confirm the `X-Webhook-Key` row saved |
| Webhook returns `422`, repeatedly | Payload shape mismatch | The raw body is in `webhook_dlq` — inspect it and adapt `PostCallWebhookPayload` |
| Nothing arrives at all | Request never left Gnani | Check the console's **Action Logs**; if empty, it is console-side, not yours |
| `ZoneInfoNotFoundError` | No IANA database | `tzdata` is pinned in `requirements.txt`; reinstall |
| `pydantic-core` build failure | Python 3.14 without wheels | Version floors in `requirements.txt` exist for this — do not lower them |
| PTP dates off by one day | `TIMEZONE` disagrees with the agent | Align them |
| Dashboard rows never update live | WebSocket blocked | Header pill shows `reconnecting`; a refresh still shows correct data |

Anything unexplained: `docker compose logs api --tail 100`. Every line carries
`call_id`, `conversation_id` and `request_id`.

---

## Data management

```bash
# keep only real Gnani calls, drop mock-generated rows
python -m scripts.clear_demo_data --dry-run
python -m scripts.clear_demo_data

# export real webhook payloads as samples (redacted)
python -m scripts.export_samples

docker compose down        # stop, keep data
docker compose down -v     # stop and wipe the database
```

Run these from the host against the exposed Mongo port:

```bash
STORAGE_BACKEND=mongo MONGO_URI=mongodb://localhost:27017 python -m scripts.clear_demo_data
```

---

## Deploying beyond a laptop

Not done here, but the shape it would take.

**Immediate blockers.** Concurrency is capped: `preCallVariables` are keyed to
the bot rather than the call, so overlapping calls on one agent would clobber
each other's variables — `CallService._trigger_lock` serialises them. Real
throughput needs per-call variable scoping from Gnani, or one agent per
concurrent stream. Secrets belong in a managed store, not `.env`. The JSON
backend is single-writer and exists for demonstration only.

**Scaling.** The API is stateless and scales horizontally behind a load
balancer. The webhook should hand off to a queue so intake stays fast and
enrichment retries independently. MongoDB needs a replica set; the unique index
on `event_id` already makes idempotency correct under concurrent delivery.
Replace the tunnel with a real ingress and TLS.

**Operations.** Structured JSON logs already carry `call_id`, `conversation_id`
and `request_id`. **`webhook_dlq` depth is the alert that matters** — a rising
queue means correlation is failing, which is invisible from the dashboard alone.
`audit_logs` gives a per-call state-transition trail.
