from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from .config import settings

# SQLite (dev) does not accept pool sizing kwargs; Postgres (prod) does.
_engine_kwargs: dict = {"pool_pre_ping": True}
if not settings.is_sqlite:
    _engine_kwargs.update(pool_size=5, max_overflow=10)

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
