"""Serving the browser app: its built files, and its page for every address that isn't the API.

The app is a client of the public API like any other; serving it from the same address keeps
cookies and the CSRF check simple. Pages get a strict Content-Security-Policy: scripts, styles,
fonts and API calls only from here, images also as data (the QR code when setting up an app).
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

from p42cloud.kernel.errors import Problem
from p42cloud.web.permissions import PUBLIC, requires

# Where the build puts it, the same relative to this file in the repository and in the image.
BUILT = Path(__file__).resolve().parents[4] / "frontend" / "dist" / "browser"

PAGE_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline';"
    " font-src 'self'; img-src 'self' data:; connect-src 'self'; manifest-src 'self';"
    " base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
# Built files have content hashes in their names: they never change.
HASHED = ".js", ".css", ".woff", ".woff2"


def _inside(folder: Path, path: str) -> Path | None:
    """The file at `path` under `folder`, never one outside it."""
    wanted = (folder / path).resolve()
    if not wanted.is_relative_to(folder) or not wanted.is_file():
        return None
    return wanted


def install(app: FastAPI, folder: Path | None = BUILT) -> None:
    """Serves the app if it has been built; without it the API works alone."""
    if folder is None or not (folder / "index.html").is_file():
        return
    root = folder.resolve()
    index = root / "index.html"

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    @requires(PUBLIC)
    async def browser_app(path: str, request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        """A built file, or the app's page, which then shows the address itself."""
        if path.startswith("api/") or path == "api":
            raise Problem(404, "not_found", "There's no such address in the API.")
        found = _inside(root, path) if path else None
        if found is not None and found != index:
            answer = FileResponse(found)
            if found.suffix in HASHED:
                answer.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return answer
        page = FileResponse(index, media_type="text/html")
        page.headers["Cache-Control"] = "no-cache"
        page.headers["Content-Security-Policy"] = PAGE_POLICY
        return page
