"""Exception handlers globales — todas las respuestas de error usan el envelope."""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.schemas.common import ApiError

logger = get_logger("apiems.errors")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(TimeoutError)
    async def timeout_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: TimeoutError
    ) -> JSONResponse:
        """Una consulta a InfluxDB que no volvió a tiempo.

        Sin este manejador la excepción sube hasta `ServerErrorMiddleware`, que
        está **por fuera** del middleware de CORS: el 500 sale sin la cabecera
        `Access-Control-Allow-Origin` y el navegador lo reporta como un problema
        de permisos. Se pierden horas mirando la configuración de CORS por un
        fallo que no tiene nada que ver.

        503 y no 500 porque es exactamente eso: un servicio del que dependemos
        no respondió a tiempo, y reintentar puede funcionar.
        """
        logger.warning("consulta_sin_respuesta", path=request.url.path)
        body = ApiError(
            message=(
                "La base de datos de mediciones no respondió a tiempo. "
                "Probá de nuevo o acotá el rango de fechas."
            ),
            error=None,
        )
        return JSONResponse(status_code=503, content=body.model_dump())

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        body = ApiError(message=str(exc.detail), error=None)
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors: list[dict[str, object]] = [
            {
                "field": ".".join(str(loc) for loc in err.get("loc", [])),
                "message": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
            for err in exc.errors()
        ]
        body = ApiError(message="Validation error", error=errors)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=body.model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("unhandled_exception", path=request.url.path)
        body = ApiError(message="Internal server error", error=None)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=body.model_dump(),
        )
