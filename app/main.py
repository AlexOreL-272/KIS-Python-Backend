import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.cars import router
from app.config import Settings
from app.database import build_engine, build_session_factory
from app.errors import CarNotFound

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        db_engine = engine if engine is not None else build_engine(settings)
        application.state.session_factory = build_session_factory(db_engine)
        application.state.engine = db_engine
        try:
            yield
        finally:
            if engine is None:
                db_engine.dispose()

    application = FastAPI(
        title="Carshop Backend",
        version="1.0.0",
        description="Car catalog management with PostgreSQL.",
        lifespan=lifespan,
        docs_url="/swagger",
        redoc_url="/redoc",
    )
    application.include_router(router)

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
    async def not_found(_request: Request, exc: CarNotFound):
        return JSONResponse(status_code=404, content={"error": "not found", "details": [str(exc)]})

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = [f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors()]
        return JSONResponse(
            status_code=422, content={"error": "validation error", "details": details}
        )

    @application.exception_handler(IntegrityError)
    async def integrity_error(_request: Request, exc: IntegrityError):
        logger.warning("Database rejected a write: %s", type(exc).__name__)
        return JSONResponse(
            status_code=409,
            content={"error": "conflict", "details": ["database constraint failed"]},
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, exc: SQLAlchemyError):
        logger.error("Database operation failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503, content={"error": "database unavailable", "details": []}
        )

    @application.get("/api/v1/healthcheck", tags=["System"])
    def healthcheck(request: Request):
        with request.app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok", "version": "1.0.0"}

    return application


app = create_app()
