"""Proceso corto para convertir HTML a PDF sin retener memoria en Gunicorn."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import unquote, urlparse


def _static_fetcher(app_root: Path):
    from weasyprint import default_url_fetcher

    static_root = (app_root / "static").resolve()

    def fetch(url, *args, **kwargs):
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.path.startswith("/static/"):
            candidate = (static_root / unquote(parsed.path[len("/static/"):])).resolve()
            try:
                candidate.relative_to(static_root)
            except ValueError:
                candidate = None
            if candidate is not None and candidate.is_file():
                return default_url_fetcher(candidate.as_uri(), *args, **kwargs)
        return default_url_fetcher(url, *args, **kwargs)

    return fetch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--app-root", required=True)
    args = parser.parse_args()

    from weasyprint import HTML

    app_root = Path(args.app_root).resolve()
    HTML(
        filename=str(Path(args.html).resolve()),
        base_url=args.base_url or str(app_root),
        url_fetcher=_static_fetcher(app_root),
    ).write_pdf(str(Path(args.output).resolve()))


if __name__ == "__main__":
    main()
