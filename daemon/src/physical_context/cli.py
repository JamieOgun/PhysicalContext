import argparse

import uvicorn

from physical_context.app import create_app
from physical_context.config import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Physical Context Layer daemon")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--local-caption", action="store_true", default=None)
    parser.add_argument("--local-embed", action="store_true", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    overrides = {key: value for key, value in vars(args).items() if value is not None}
    settings = Settings(**overrides)

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
