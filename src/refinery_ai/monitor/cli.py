"""Start a loopback-only monitor without initializing execution clients."""

from __future__ import annotations

import socket
import threading
import webbrowser
from pathlib import Path
from typing import Annotated

import typer


def serve_monitor(
    repository: Path,
    state_directory: Path,
    port: int,
    open_browser: bool,
) -> None:
    import uvicorn

    from refinery_ai.monitor.server import create_app

    url = f"http://127.0.0.1:{port}"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as error:
            raise OSError(
                f"Cannot start monitor on port {port}; it may be already in use"
            ) from error
        listener.listen(128)
        application = create_app(repository, state_directory, allowed_host=f"127.0.0.1:{port}")
        server = uvicorn.Server(uvicorn.Config(application, log_level="warning"))
        stop = threading.Event()

        def launch_browser() -> None:
            while not stop.wait(0.05):
                if server.started:
                    webbrowser.open(url)
                    return

        browser = None
        if open_browser:
            browser = threading.Thread(target=launch_browser, daemon=True)
            browser.start()
        typer.echo(f"Refinery monitor: {url}")
        typer.echo(f"Repository: {repository.resolve()} · Read-only · Ctrl-C to stop")
        try:
            server.run(sockets=[listener])
        finally:
            stop.set()
            if browser is not None:
                browser.join(timeout=1)


def monitor(
    repository: Annotated[
        Path,
        typer.Option(
            "--repository",
            "-r",
            exists=True,
            file_okay=False,
            help="Repository whose recorded runs to monitor",
        ),
    ] = Path("."),
    port: Annotated[int, typer.Option(min=1, max=65535, help="Local dashboard port")] = 8765,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Print URL without opening a browser")
    ] = False,
) -> None:
    """Open a local dashboard for live and historical workflow runs."""
    from refinery_ai.config import Settings

    try:
        serve_monitor(repository.resolve(), Settings().state_directory, port, not no_browser)
    except (OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
