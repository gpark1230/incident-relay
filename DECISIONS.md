# Decisions

A running log of real decisions made while building IncidentRelay — what was considered, what was chosen, and why. Same format as IncidentDesk's decision log.

## RQ over Celery

Same category of tool (Redis-backed job queue with retry support), but far less setup: no separate broker/backend config, no Celery-specific serialization concerns, and the whole worker is `Worker(["notifications"], connection=redis_conn).work()`. At this project's scale (one queue, one job type), Celery's extra flexibility isn't buying anything, and RQ is much easier to explain cleanly in an interview.

## A listener process bridging IncidentDesk's raw Redis list into an RQ queue, instead of one worker doing both

IncidentDesk's event-publishing change pushes plain JSON via `RPUSH` onto a Redis list (`incident_events`) — not RQ's own job format. RQ jobs are enqueued via `Queue.enqueue()`, which stores job metadata as a Redis hash and a job ID on the queue's internal list; a raw JSON blob on a plain list isn't a job RQ can dequeue directly.

Considered making IncidentDesk itself call into RQ's enqueue format, but that would mean importing RQ (or hand-rolling its wire format) into IncidentDesk just to satisfy this service's internal choice of job library — a coupling this project's architectural boundary explicitly rules out. IncidentDesk shouldn't need to know or care that the consuming service happens to use RQ internally.

So there are two processes on the consuming side:
- **listener** (`app/listener.py`) — `BLPOP`s the raw `incident_events` list, writes a `NotificationAttempt` row (status `pending`), then calls `Queue.enqueue()` to hand the event to RQ.
- **worker** (`app/worker.py`) — a normal RQ worker that executes the actual webhook-send job, with RQ's `Retry` handling backoff.

This keeps the integration contract with IncidentDesk to exactly one thing — a Redis list name and a JSON shape — while still getting RQ's retry/backoff machinery for the part that actually needs it (sending the notification).

## One durable `NotificationAttempt` row per event, updated in place across retries

The job function takes `(attempt_id, event)`, not just `event`. The row is created once, before the job is enqueued, and every retry (same job, same args, re-run by RQ's scheduler) loads and updates that same row — incrementing `retry_count`, recording `error_message`, and flipping `status` to `sent` or `failed`. This was a deliberate choice over creating a new row per attempt: it makes "how many times did we retry this specific notification" a direct read of one row, not an aggregation across rows tied together by some other key.

## Bug: RQ retries silently didn't fire

`Worker.work()` defaults `with_scheduler=False`. Without it, jobs enqueued with `Retry(interval=[...])` fail once and never get rescheduled — no error, they just don't retry. Caught this by actually pushing a fake event through the full local Docker stack and watching `retry_count` stay at 1 past the 10-second backoff window. Fix was `worker.work(with_scheduler=True)`.

## Bug: enum created twice in the first Alembic migration

Manually calling `notification_status.create(op.get_bind(), checkfirst=True)` before `op.create_table(...)` caused a `DuplicateObject` error — `create_table` already creates any `Enum` type referenced by its own columns. Removed the manual `.create()` call; `create_table` handles it.

## Slack's incoming-webhook JSON shape (`{"text": ...}`), not Discord's

CLAUDE.md allowed either. Slack's incoming webhook payload is `{"text": "..."}`; Discord's is `{"content": "..."}`. Went with Slack's shape first since it's the more common one recruiters would recognize; swapping to Discord later is a one-line change in `notifier.py`.

## Local Docker Compose uses non-default host ports (5433, 6380)

So this stack can run alongside IncidentDesk's own Postgres (5432) and Redis (6379) on the same machine during local development/integration testing, without port collisions. Inside the Docker network, services still talk on the standard 5432/6379.

## No Slack user directory — notifications address `user:{id}`, not a real DM

Building real per-recipient Slack routing would require a Slack app with OAuth scopes and a stored Slack-user mapping — significantly more setup than a single incoming webhook for a project at this scale. Documented as a known limitation rather than silently faked.

## Token bucket implemented as a single atomic Redis Lua script, not GET-then-SET

A plain `GET tokens` → compute in Python → `SET tokens` round trip has a race: two listener processes (or even two events processed back to back before the first write lands) could both read the same token count and both allow, over-admitting past capacity. `app/rate_limiter.py`'s `_TOKEN_BUCKET_SCRIPT` does the read, refill-by-elapsed-time math, and conditional decrement inside one `EVAL`, which Redis runs atomically — no other command executes on that Redis instance in between. This is a real "how would you avoid a race condition in a distributed rate limiter" answer, not just "we used Redis."

Tokens refill continuously (`elapsed_seconds * capacity/window`) rather than resetting all-at-once at fixed window boundaries — a classic token bucket, not a fixed-window counter, so a recipient isn't either fully blocked or fully open at a window edge.

**Bug hit:** `fakeredis` (used in tests to avoid a live Redis) doesn't support `EVAL` unless the `lupa` package (an embedded Lua-in-Python) is installed — it's an optional extra, not a hard fakeredis dependency. Tests failed with `unknown command 'eval'` until `lupa` was added to `requirements.txt`.

## Rate-limited attempts get their own status, not reused `failed`

Added `NotificationStatus.rate_limited` (migration `a1c2d3e4f5g6`) rather than recording a rate-limited notification as `failed` with a note in `error_message`. A recruiter or interviewer can `GET /notifications?status=rate_limited` and see direct proof the limiter fired, without reading error-message text to distinguish "Slack rejected this" from "we deliberately didn't send this." Postgres enums can't drop a value, so `downgrade()` is a documented no-op — acceptable since this is additive.

## Rate limiter key is per-recipient only (not per-incident, not both)

`RATE_LIMIT_MAX_TOKENS`/`RATE_LIMIT_WINDOW_SECONDS` cap notifications *to one person* per window, regardless of which incident triggered them. This matches the stated goal directly — "a burst of incident updates doesn't spam someone" is about protecting a human's notification volume, not about rate-limiting an incident's event stream. A per-incident (or combined) key was considered and rejected: it would let a single very active incident spam one recipient with updates about *other* incidents while technically staying "under limit" per incident.

## Cache invalidation happens even when the triggering notification is rate-limited

`incident.updated` cache invalidation in the listener runs unconditionally, before the rate-limit check — it doesn't matter whether the *notification* for that update goes out; the cached incident data is stale the moment IncidentDesk says it changed, full stop. Coupling invalidation to notification delivery would have been a real bug: a recipient who's currently rate-limited would keep seeing stale cached incident data indefinitely.

## Verified caching live against a stub IncidentDesk server, not just mocks

Unit tests mock `httpx.get`, which proves the code path but not that a real HTTP round trip to another service actually caches and invalidates correctly. To verify for real (same standard applied to the queue/worker/rate-limiter milestones), a tiny stand-in HTTP server was run on the host (`http.server`, one route, an incrementing hit counter in the response body) with `INCIDENT_DESK_API_URL` pointed at it via Docker's `host.docker.internal`. Confirmed: first `GET /incidents/5` was a MISS hitting the stub (`upstream_hit_count` incremented, `X-Cache: MISS`), the second was a HIT with no upstream call (`X-Cache: HIT`, same `upstream_hit_count`), and after pushing a real `incident.updated` event through Redis, the next `GET` was a MISS again with `upstream_hit_count` incremented — proving the listener's invalidation actually took effect, not just that the code compiles.

## Real Slack incoming webhook confirmed end-to-end

Swapped the placeholder `NOTIFY_WEBHOOK_URL` for a real incoming webhook (created via api.slack.com/apps → Incoming Webhooks → added to a live channel), pushed a real `incident.created` event through the full pipeline, and confirmed: `httpx` got `HTTP 200 OK` from `hooks.slack.com`, the `notification_attempts` row landed as `status = sent`, and the message was visually confirmed in the live Slack channel. No code changes were needed — `app/notifier.py` was written against Slack's real webhook contract from the start, so this was purely a config swap.

## Railway: five services (Postgres, Redis, app, worker, listener), matching local exactly

Railway's free plan caps a project at 3 resources. Postgres + Redis + `app` already hit that cap before `worker` and `listener` could be created — the first attempt at `railway add --service worker` failed with "Free plan resource provision limit exceeded." The initial plan was to combine worker and listener into one container via `scripts/run_worker_and_listener.sh` (a small supervisor script backgrounding both processes and exiting — taking the container down for a clean joint restart — if either died) to stay free. After upgrading to the Hobby plan, that workaround was dropped in favor of what was actually tested locally: `worker` and `listener` as fully separate services, identical to `docker-compose.yml`. The supervisor script is still in the repo (unused) as a documented option if resource constraints come up again.

## Bug: `alembic upgrade head` failed on first deploy — DATABASE_URL was never set

The `app` service's first deploy failed in its `preDeployCommand` step with `psycopg2.OperationalError: connection to server at "localhost"... port 5433 failed: Connection refused` — it was trying to reach the local-dev default from `app/config.py`, because `DATABASE_URL` and `REDIS_URL` hadn't actually been wired to the Postgres/Redis addons yet (`railway variable set DATABASE_URL='${{Postgres.DATABASE_URL}}'` was a step that got skipped while sorting out source/build config first). Caught by reading the deploy logs (`railway logs <deployment-id> --deployment`) rather than assuming a build failure meant a build problem — the image had built and pushed successfully; the crash was in the pre-deploy step that runs after. Fixed by setting the variables, then redeploying app first (so the migration runs) before worker/listener.

## Bug: `source.repo` needs `owner/repo`, not a full URL — and the dot-path CLI edit silently no-ops

`railway environment edit --service-config <id> source.repo "https://github.com/gpark1230/incident-relay"` returned `{"committed":false,"message":"No changes to apply"}` with zero indication anything was wrong — no error, just silent non-application. Two separate issues, found by comparing against an already-working service (IncidentDesk's `api`, queried via the GraphQL API directly): the value has to be the short `owner/repo` form, and even with the correct value, the dot-path `--service-config` form didn't commit the change at all — the `--json '{"services": {...}}}'` JSON-patch form did (`committed: true`). Switched to JSON patches for all subsequent service config changes.

## Bug: `build.builder: DOCKERFILE` isn't a valid value in this API

The skill reference's config schema listed `DOCKERFILE` as a `build.builder` option; a GraphQL introspection query (`__type(name: "Builder") { enumValues { name } }`) shows the actual enum is `HEROKU | NIXPACKS | PAKETO | RAILPACK` — no `DOCKERFILE` value exists. Setting it silently had no effect (same JSON-patch call that worked for other fields left `builder: RAILPACK` unchanged). Turned out not to matter: Railway's Railpack builder auto-detects a `Dockerfile` at the repo root and builds with `buildkit` regardless of the `builder` label, confirmed by reading actual build logs showing Docker layer builds (`[4/5] RUN pip install...`, `[5/5] COPY . .`) rather than a Railpack-assembled build plan.

## Measured the cache hit rate instead of guessing a number

Needed an honest number for how much the read-through cache actually cuts repeat reads against IncidentDesk — not a plausible-sounding guess. `scripts/load_test_cache.py` simulates a realistic access pattern: 5 "active" incidents looked up repeatedly at random (like several on-call engineers checking the same incidents, or a dashboard polling), one request every 0.5s for 90 seconds (three full 30s TTL cycles). It reads the real `X-Cache` response header IncidentRelay already sets — no separate instrumentation needed — and cross-checks the reported miss count against the test upstream's own per-incident request counter, so the number isn't just self-reported by the thing being measured.

Result: **175 requests, 160 hits, 15 misses — 91.4% cache hit rate**, with the upstream cross-check matching exactly (15 misses reported = 15 requests the upstream actually received). Run locally against the same stub-IncidentDesk setup used to verify invalidation, since the live Railway deployment can't be used for this yet — see the bug below.

## Fixed: the cache proxy 500'd instead of passing through IncidentDesk's real status code

Found while trying to run the load test against the live Railway deployment instead of locally: `GET /incidents/{id}` returned a bare `500` there, because IncidentDesk requires a Bearer token IncidentRelay's proxy never sends, IncidentDesk correctly returns `401`, and `get_incident()`'s `upstream.raise_for_status()` turned that into an unhandled exception instead of a passed-through `401`. Fixed by replacing the `404`-only special case + `raise_for_status()` with a general `status_code >= 400` check that re-raises IncidentRelay's own `HTTPException` at IncidentDesk's real status code, carrying IncidentDesk's real response body as `detail` — and, importantly, only caching on success, so an error response never gets cached as if it were real incident data. Verified against the real live IncidentDesk (not just a mock): `GET /incidents/1` through the local proxy now correctly returns `401 {"detail":{"detail":"Not authenticated"}}` instead of a bare `500`.

Real credentials for IncidentRelay to call IncidentDesk's API (service token? shared secret?) weren't wired up yet at that point — that's a separate, bigger scope decision than "return the right status code." Resolved below.

## IncidentDesk auth: a dedicated service account, not shared personal credentials

IncidentDesk only has one auth mechanism — email/password login at `POST /auth/login` returning a JWT (`app/auth.py`, `OAuth2PasswordBearer` + `python-jose`), no separate machine-to-machine flow. Rather than give IncidentRelay Gavin's own login, or hand-create a row directly in IncidentDesk's database, a dedicated service account was created through IncidentDesk's own public `POST /auth/signup` endpoint — the same path any real user goes through, so it's an ordinary account with ordinary constraints, not a special-cased backdoor. `GET /incidents/{incident_id}` only requires `get_current_user` (any authenticated user, no `require_role` check), so the default signup role, `viewer`, is already sufficient — no elevated permissions needed or requested.

`app/incident_desk_auth.py` logs in once and caches the resulting JWT in Redis for 55 minutes — a little under IncidentDesk's own 60-minute expiry (`ACCESS_TOKEN_EXPIRE_MINUTES` in `app/auth.py`), so IncidentRelay always refreshes *before* IncidentDesk itself would reject the token, rather than finding out via a failed request. `GET /incidents/{id}` retries exactly once on a `401` (invalidating the cached token first) to absorb clock-skew or early-revocation edge cases, then gives up and passes the real `401` through rather than retrying forever.

Verified against the real live IncidentDesk, not a mock: `GET /incidents/1` through the local proxy now returns real incident data (`"Ransomware alert from CrowdStrike on FIN-WKS-014"`) on a cache MISS, and serves it from cache with zero upstream calls on the next request — the full read-through cache, working end-to-end against production IncidentDesk for the first time.

## Not yet decided / not yet built

(nothing currently)
