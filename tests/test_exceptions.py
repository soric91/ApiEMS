"""El catch-all de Exception no tiene ruta real que lo dispare en la app
(todas las rutas manejan sus propios errores); se prueba con una app
mínima aislada que registra los mismos handlers y sí tiene una ruta que
revienta a propósito."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers


def _make_broken_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:  # pyright: ignore[reportUnusedFunction]
        raise ValueError("algo inesperado revienta acá")

    return app


def test_unhandled_exception_returns_500_envelope() -> None:
    app = _make_broken_app()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Internal server error"
