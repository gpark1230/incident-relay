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

## Railway: worker + listener combined into one service, not two

Locally, the worker and listener are separate processes (`docker-compose.yml` still runs them as two services — that's the real, intended architecture, and it's what's documented and tested throughout this file). On Railway, provisioning hit the free plan's resource limit at 3 resources (Postgres, Redis, app), so `worker` and `listener` couldn't both get their own service.

Rather than pay to preserve a topology detail that doesn't change the actual design, `scripts/run_worker_and_listener.sh` runs both processes in one container: it backgrounds `python -m app.worker` and `python -m app.listener`, then `wait -n` on either PID. If either process exits (crash or otherwise), the script exits too, which stops the container — Railway's restart policy then restarts the whole thing, bringing both processes back up together rather than leaving one silently dead while the other keeps running. This is a deployment-environment constraint, not an architecture change: the code, the queue, and the Redis list contract are unchanged: only how many OS processes happen to share one container differs between local dev and this Railway deployment.

## Not yet decided / not yet built

(nothing currently)
