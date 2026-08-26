import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.routes import companies, health, leads, search
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import Database
from app.jobs.outbox import OutboxDispatcher, RQSearchQueue, SearchQueue

logger = get_logger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    queue: SearchQueue | None = None,
    start_dispatcher: bool = True,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    owns_database = database is None
    resolved_database = database or Database(resolved_settings.database_url)
    resolved_queue = queue or RQSearchQueue.from_settings(resolved_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        dispatcher_task: asyncio.Task[None] | None = None
        if start_dispatcher:
            dispatcher = OutboxDispatcher(
                resolved_database.session_factory,
                resolved_queue,
                job_timeout_seconds=resolved_settings.rq_job_timeout_seconds,
            )
            dispatcher_task = asyncio.create_task(_dispatch_loop(dispatcher, stop))
        try:
            yield
        finally:
            stop.set()
            if dispatcher_task is not None:
                dispatcher_task.cancel()
                with suppress(asyncio.CancelledError):
                    await dispatcher_task
            if owns_database:
                await resolved_database.dispose()

    application = FastAPI(
        title="Client Parser Map",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.database = resolved_database
    application.state.queue = resolved_queue
    application.include_router(health.router)
    application.include_router(search.router)
    application.include_router(leads.router)
    application.include_router(companies.router)
    return application


async def _dispatch_loop(dispatcher: OutboxDispatcher, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await dispatcher.dispatch_pending()
        except Exception as error:
            logger.exception(
                "outbox_dispatch_failed",
                error_code=type(error).__name__.upper(),
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except TimeoutError:
            continue


app = create_app()
