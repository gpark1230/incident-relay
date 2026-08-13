import logging

from redis import Redis
from rq import Worker

from app.config import settings

logging.basicConfig(level=logging.INFO)


def run() -> None:
    redis_conn = Redis.from_url(settings.redis_url)
    worker = Worker(["notifications"], connection=redis_conn)
    # with_scheduler=True is required for RQ's Retry(interval=...) backoff to
    # actually reschedule failed jobs — without it, retries are silently dropped.
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    run()
