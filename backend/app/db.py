from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from .config import settings

# SQLite (dev) does not accept pool sizing kwargs; Postgres (prod) does.
_engine_kwargs: dict = {"pool_pre_ping": True}
if settings.is_sqlite:
    # NullPool on SQLite — a correctness fix, not a tuning knob. This engine is a module-level
    # global, but pytest-asyncio runs every test on a FRESH event loop (asyncio_mode="auto",
    # function-scoped by default). A pooled aiosqlite connection opened on one loop and handed
    # to the next is undefined behaviour: it surfaced as "the garbage collector is trying to
    # clean up non-checked-in connection" and, once a run allocated enough to make the GC fire
    # mid-query, as a hard interpreter access violation part-way through the suite.
    # NullPool opens and closes per checkout, so no connection outlives the loop that made it.
    # Cost is nil where it applies: SQLite is dev and tests only — prod is Postgres, which
    # keeps the real pool below.
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs.update(pool_size=5, max_overflow=10)

engine = create_async_engine(settings.async_database_url, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
