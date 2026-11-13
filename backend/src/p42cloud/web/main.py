"""Running the web app: `p42cloud-web` (the installation's `web` service).

The web app and the command line sit side by side and never import each other, so the app has
its own command.
"""

import argparse

import uvicorn

from p42cloud.kernel.config import get_settings
from p42cloud.web.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p42cloud-web", description="Runs the HTTP API.")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - inside the container
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    settings = get_settings()
    trusted = settings.trusted_proxies
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        proxy_headers=trusted is not None,
        forwarded_allow_ips=trusted or "",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
