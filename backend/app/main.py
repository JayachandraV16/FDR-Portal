from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import auth, banks, fdrs, health, institutions, users
from app.api.deps import NotAuthenticated
from app.config import get_settings
from app.security.cookies import clear_session_cookie


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="FDR & Security Deposit Portal API",
        version=__version__,
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(NotAuthenticated)
    async def _not_authenticated(_request: Request, exc: NotAuthenticated) -> JSONResponse:
        # Generic on purpose: expired, revoked, unknown and malformed cookies are indistinguishable.
        response = JSONResponse({"detail": "Not authenticated"}, status_code=401)
        response.headers["Cache-Control"] = "no-store"
        clear_session_cookie(response, exc.settings)
        return response

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(institutions.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(banks.router, prefix="/api/v1")
    app.include_router(fdrs.router, prefix="/api/v1")
    return app


app = create_app()
