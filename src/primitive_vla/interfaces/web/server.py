"""Desktop-owned loopback server; port selection and process ownership stay with the launcher."""
import argparse
import importlib
import socket

import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--factory")
    args = parser.parse_args()
    if args.factory:
        module, name = args.factory.split(":", 1)
        app = getattr(importlib.import_module(module), name)()
    else:
        app = create_app(workspace=args.workspace)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        address = f"http://127.0.0.1:{listener.getsockname()[1]}"
        print(f"PRIMITIVE_LISTENING {address}", flush=True)
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
        server.run(sockets=[listener])


if __name__ == "__main__":
    main()
