import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.cars import router
from app.cache import Cache, RedisCache
from app.config import Settings
from app.database import build_engine, build_session_factory
from app.errors import CarNotFound, CarOperationNotFound, CarOutOfStock, CarStockLimitReached

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, *, engine: Engine | None = None, cache: Cache | None = None
) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        db_engine = engine if engine is not None else build_engine(settings)
        redis_client = None
        app_cache = cache
        if app_cache is None:
            redis_client = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
            )
            app_cache = RedisCache(redis_client, settings.cache_ttl_seconds)
        application.state.cache = app_cache
        application.state.redis = redis_client
        application.state.session_factory = build_session_factory(db_engine)
        application.state.engine = db_engine
        try:
            yield
        finally:
            if redis_client is not None:
                redis_client.close()
            if engine is None:
                db_engine.dispose()

    application = FastAPI(
        title="Carshop Backend",
        version="1.0.0",
        description="Car catalog management with PostgreSQL and Redis cache-aside.",
        lifespan=lifespan,
        docs_url="/swagger",
        redoc_url="/redoc",
    )
    application.include_router(router)

    default_openapi = application.openapi

    def custom_openapi() -> dict[str, Any]:
        schema = default_openapi()
        # FastAPI adds 422 automatically even with a custom validation handler.
        for path in schema["paths"].values():
            for operation in path.values():
                responses = operation.get("responses", {})
                if str(status.HTTP_400_BAD_REQUEST) in responses:
                    responses.pop(str(status.HTTP_422_UNPROCESSABLE_CONTENT), None)
        return schema

    application.openapi = custom_openapi

    @application.middleware("http")
    async def log_request(request: Request, call_next):
        request_id = uuid4().hex
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s status=%s duration_ms=%.1f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.monotonic() - started) * 1000,
            request_id,
        )
        return response

    @application.exception_handler(CarNotFound)
    @application.exception_handler(CarOperationNotFound)
    async def not_found(_request: Request, exc: CarNotFound | CarOperationNotFound):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "not found", "details": [str(exc)]},
        )

    @application.exception_handler(CarOutOfStock)
    @application.exception_handler(CarStockLimitReached)
    async def stock_conflict(_request: Request, exc: CarOutOfStock | CarStockLimitReached):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "conflict", "details": [str(exc)]},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = [f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors()]
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "validation error", "details": details},
        )

    @application.exception_handler(IntegrityError)
    async def integrity_error(_request: Request, exc: IntegrityError):
        logger.warning("Database rejected a write: %s", type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "conflict", "details": ["database constraint failed"]},
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, exc: SQLAlchemyError):
        logger.error("Database operation failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "database unavailable", "details": []},
        )

    @application.get("/api/v1/healthcheck", tags=["System"])
    def healthcheck(request: Request):
        with request.app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        redis_client = request.app.state.redis
        cache_status = "injected"
        if redis_client is not None:
            try:
                redis_client.ping()
                cache_status = "ok"
            except RedisError:
                cache_status = "unavailable"
        return {"status": "ok", "database": "ok", "cache": cache_status, "version": "1.0.0"}

    return application


app = create_app()
