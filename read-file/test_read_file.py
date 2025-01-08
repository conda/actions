from __future__ import annotations

from argparse import Namespace
from contextlib import nullcontext, suppress
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from json.decoder import JSONDecodeError
from pathlib import Path
from queue import Queue
from socket import IPPROTO_IPV6, IPV6_V6ONLY
from threading import Thread
from typing import TYPE_CHECKING

import pytest

from read_file import dump_output, get_output, parse_args, parse_content, read_file

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Literal

    from pytest import MonkeyPatch


DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def test_server() -> Iterator[ThreadingHTTPServer]:
    """
    Run a test server on a random port. Inspect returned server to get port,
    shutdown etc.

    See https://github.com/conda/conda/blob/52b6393d6331e8aa36b2e23ab65766a980f381d2/tests/http_test_server.py
    """

    class DualStackServer(ThreadingHTTPServer):
        daemon_threads = False  # These are per-request threads
        allow_reuse_address = True  # Good for tests
        request_queue_size = 64  # Should be more than the number of test packages

        def server_bind(self):
            # suppress exception when protocol is IPv4
            with suppress(Exception):
                self.socket.setsockopt(IPPROTO_IPV6, IPV6_V6ONLY, 0)
            return super().server_bind()

        def finish_request(self, request, client_address):
            self.RequestHandlerClass(request, client_address, self, directory=DATA)

        def __str__(self) -> str:
            host, port = self.socket.getsockname()[:2]
            url_host = f"[{host}]" if ":" in host else host
            return f"http://{url_host}:{port}/"

    def start_server(queue: Queue):
        with DualStackServer(("localhost", 0), SimpleHTTPRequestHandler) as httpd:
            queue.put(httpd)
            print(f"Serving ({httpd}) ...")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nKeyboard interrupt received, exiting.")

    started: Queue[ThreadingHTTPServer] = Queue()
    Thread(target=start_server, args=(started,), daemon=True).start()
    yield (server := started.get(timeout=1))
    server.shutdown()


def test_parse_args_file(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    # file is required
    with pytest.raises(SystemExit):
        assert parse_args([])
    with pytest.raises(SystemExit):
        assert parse_args(["--parser=json"])
    with pytest.raises(SystemExit):
        assert parse_args(["--default=text"])
    assert parse_args(["file"])


@pytest.mark.parametrize(
    "parser,default",
    [
        (None, None),
        ("json", None),
        ("yaml", None),
        (None, "text"),
        ("json", "text"),
        ("yaml", "text"),
    ],
)
def test_parse_args_optional(parser: str | None, default: str | None) -> None:
    # other args are optional
    assert parse_args(
        [
            "file",
            *([f"--parser={parser}"] if parser else []),
            *([f"--default={default}"] if default else []),
        ]
    ) == Namespace(file="file", parser=parser, default=default)


@pytest.mark.parametrize("source", ["local", "test_server"])
@pytest.mark.parametrize(
    "path,default,raises",
    [
        ("json.json", None, False),
        ("json.json", "default", False),
        ("yaml.yaml", None, False),
        ("yaml.yaml", "default", False),
        ("missing", None, True),
        ("missing", "default", False),
    ],
)
def test_read_file(
    test_server: ThreadingHTTPServer,
    source: Literal["local", "test_server"],
    path: str,
    default: str | None,
    raises: bool,
) -> None:
    content = default
    with suppress(FileNotFoundError):
        content = (DATA / path).read_text()

    with pytest.raises(FileNotFoundError) if raises else nullcontext():
        uri = f"{DATA if source == "local" else test_server}/{path}"
        assert read_file(uri, default) == content


@pytest.mark.parametrize(
    "path,parser,raises",
    [
        ("json.json", "json", False),
        ("json.json", "yaml", False),
        ("json.json", "unknown", ValueError),
        ("yaml.yaml", "json", JSONDecodeError),
        ("yaml.yaml", "yaml", False),
        ("yaml.yaml", "unknown", ValueError),
    ],
)
def test_parse_content(
    path: str, parser: Literal["json", "yaml"], raises: bool
) -> None:
    content = (DATA / path).read_text()
    expected = (DATA / "json.json").read_text().strip()
    with pytest.raises(raises) if raises else nullcontext():
        assert parse_content(content, parser) == expected


def test_get_output() -> None:
    assert get_output("content") == (
        "content<<GITHUB_OUTPUT_content\ncontent\nGITHUB_OUTPUT_content\n"
    )


def test_dump_output(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    # no-op if GITHUB_OUTPUT is not set
    dump_output("noop")

    # set GITHUB_OUTPUT and check for content
    monkeypatch.setenv("GITHUB_OUTPUT", output := tmp_path / "output")

    dump_output("content")
    assert output.read_text() == (content := get_output("content"))

    dump_output("more")
    assert output.read_text() == content + get_output("more")
