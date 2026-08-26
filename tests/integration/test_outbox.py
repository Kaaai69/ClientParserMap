from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.enums import SourceName
from app.db.base import Base
from app.db.models import JobOutbox
from app.db.repositories import SearchJobRepository
from app.jobs.outbox import OutboxDispatcher
from app.schemas.domain import SearchCriteria


class FlakyQueue:
    def __init__(self) -> None:
        self.available = False
        self.job_ids: list[str] = []

    async def enqueue_search(
        self,
        job_id: int,
        deterministic_id: str,
        job_timeout: int,
    ) -> None:
        if not self.available:
            raise ConnectionError("redis unavailable")
        self.job_ids.append(deterministic_id)

    def recover(self) -> None:
        self.available = True


async def database_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_dispatcher_retries_unsent_row_after_redis_recovers() -> None:
    async for session_factory in database_factory():
        async with session_factory() as session:
            job = await SearchJobRepository(session).create_with_outbox(
                SearchCriteria(city="Москва", query="детейлинг"),
                (SourceName.GOOGLE,),
            )
            await session.commit()

        queue = FlakyQueue()
        dispatcher = OutboxDispatcher(session_factory, queue)
        assert await dispatcher.dispatch_pending() == 0
        queue.recover()
        assert await dispatcher.dispatch_pending() == 1
        assert queue.job_ids == [f"search:{job.id}"]

        async with session_factory() as session:
            outbox = await session.scalar(select(JobOutbox))
            assert outbox is not None
            assert outbox.attempt_count == 2
            assert outbox.published_at is not None
            assert outbox.last_error is None
