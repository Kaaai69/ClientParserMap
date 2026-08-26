from redis import Redis
from rq import Queue, Worker

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(settings.rq_queue_name, connection=connection)
    Worker([queue], connection=connection).work(with_scheduler=False)


if __name__ == "__main__":
    main()
