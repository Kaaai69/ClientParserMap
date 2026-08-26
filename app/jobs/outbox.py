import asyncio
from datetime import UTC, datetime
from typing import Protocol

from redis import Redis
from rq import Queue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import JobOutbox


class SearchQueue(Protocol):
    async def enqueue_search(
        self,
        job_id: int,
        deterministic_id: str,
        job_timeout: int,
    ) -> None: ...


class RQSearchQueue:
    def __init__(self, queue: Queue) -> None:
        self._queue = queue

    @classmethod
    def from_settings(cls, settings: Settings) -> "RQSearchQueue":
        connection = Redis.from_url(settings.redis_url)
        return cls(Queue(settings.rq_queue_name, connection=connection))

    async def enqueue_search(
        self,
        job_id: int,
        deterministic_id: str,
        job_timeout: int,
    ) -> None:
        await asyncio.to_thread(
            self._enqueue_sync,
            job_id,
            deterministic_id,
            job_timeout,
        )

    async def ping(self) -> bool:
        return bool(await asyncio.to_thread(self._queue.connection.ping))

    def _enqueue_sync(self, job_id: int, deterministic_id: str, timeout: int) -> None:
        if self._queue.fetch_job(deterministic_id) is not None:
            return
        from app.jobs.tasks import run_search_job

        self._queue.enqueue(
            run_search_job,
            job_id,
            job_id=deterministic_id,
            job_timeout=timeout,
            result_ttl=86_400,
            failure_ttl=604_800,
        )


class OutboxDispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        queue: SearchQueue,
        *,
        job_timeout_seconds: int = 7200,
        batch_size: int = 50,
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue
        self._job_timeout_seconds = job_timeout_seconds
        self._batch_size = batch_size

    async def dispatch_pending(self) -> int:
        published = 0
        async with self._session_factory() as session, session.begin():
            rows = list(
                (
                    await session.scalars(
                        select(JobOutbox)
                        .where(JobOutbox.published_at.is_(None))
                        .order_by(JobOutbox.id)
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for row in rows:
                row.attempt_count += 1
                row.last_attempt_at = datetime.now(UTC)
                try:
                    await self._queue.enqueue_search(
                        row.search_job_id,
                        f"search:{row.search_job_id}",
                        self._job_timeout_seconds,
                    )
                except Exception as error:
                    row.last_error = _safe_error(error)
                    continue
                row.published_at = datetime.now(UTC)
                row.last_error = None
                published += 1
        return published


def _safe_error(error: Exception) -> str:
    return f"{type(error).__name__}: queue publication failed"[:1000]
