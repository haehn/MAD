from pathlib import Path
from mad.audit_store import AuditStore
from mad.tool_result import ToolResult
import contextlib
import http.server
import re
import shlex
import socket
import socketserver
import subprocess
import threading


class Workspace:
    IGNORED_NAMES = {
        ".git",
        ".mad",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".venv",
        "venv",
    }

    def __init__(self, path):
        self.root = Path(path).expanduser().resolve()

        self.root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.mad = self.root / ".mad"
        self.audit = self.mad / "audit"

        self.audit.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.audit_store = AuditStore(self.root)
        self.servers = {}

    #
    # Paths
    #

    def _safe_name(self, value):
        return re.sub(
            r"[^A-Za-z0-9_.-]+",
            "_",
            value,
        )

    def _resolve(self, relative_path):
        relative_path = relative_path.strip()

        if not relative_path:
            return self.root

        path = (
            self.root
            / relative_path
        ).resolve()

        if (
            path != self.root
            and self.root not in path.parents
        ):
            raise ValueError(
                f"Path escapes workspace: {relative_path}"
            )

        return path

    def _resolve_agent_path(
        self,
        relative_path,
    ):
        path = self._resolve(
            relative_path
        )

        if (
            path == self.mad
            or self.mad in path.parents
        ):
            raise ValueError(
                "Agents cannot access the .mad directory."
            )

        return path

    def exists(
        self,
        relative_path,
    ):
        return self._resolve_agent_path(
            relative_path
        ).exists()

    #
    # File tools
    #

    def list_files(
        self,
        relative_path=".",
    ):
        path = self._resolve_agent_path(
            relative_path
        )

        if not path.exists():
            raise FileNotFoundError(
                f"Path does not exist: {relative_path}"
            )

        if path.is_file():
            return str(
                path.relative_to(
                    self.root
                )
            )

        results = []

        for item in sorted(
            path.iterdir(),
            key=lambda p: p.name.lower(),
        ):
            #
            # Completely hide internal/noisy directories.
            #

            if item.name in self.IGNORED_NAMES:
                continue

            relative = item.relative_to(
                self.root
            )

            if item.is_dir():
                results.append(
                    f"{relative}/"
                )
            else:
                results.append(
                    str(relative)
                )

        if not results:
            return "(directory is empty)"

        return "\n".join(
            results
        )

    def read(
        self,
        relative_path,
    ):
        path = self._resolve_agent_path(
            relative_path
        )

        if not path.exists():
            raise FileNotFoundError(
                f"File does not exist: {relative_path}"
            )

        if not path.is_file():
            raise ValueError(
                f"Not a file: {relative_path}"
            )

        return path.read_text(
            encoding="utf-8"
        )

    def write(
        self,
        relative_path,
        content,
    ):
        path = self._resolve_agent_path(
            relative_path
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            content,
            encoding="utf-8",
        )

        return (
            f"Wrote {len(content)} characters "
            f"to {path.relative_to(self.root)}"
        )

    def append(
        self,
        relative_path,
        content,
    ):
        path = self._resolve_agent_path(
            relative_path
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with path.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(
                content
            )

        return (
            f"Appended {len(content)} characters "
            f"to {path.relative_to(self.root)}"
        )

    #
    # Commands
    #

    def run(
        self,
        command,
        timeout=120,
        structured=False,
    ):
        arguments = shlex.split(
            command
        )

        if not arguments:
            raise ValueError(
                "Empty command."
            )

        result = subprocess.run(
            arguments,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output = [
            f"Command: {command}",
            f"Return code: {result.returncode}",
        ]

        if result.stdout:
            output.extend(
                [
                    "",
                    "STDOUT:",
                    result.stdout,
                ]
            )

        if result.stderr:
            output.extend(
                [
                    "",
                    "STDERR:",
                    result.stderr,
                ]
            )

        text = "\n".join(
            output
        )

        if len(text) > 20000:
            text = (
                text[:20000]
                + "\n\n[output truncated]"
            )

        if structured:
            return ToolResult(result.returncode == 0, text)
        return text

    #
    # Background servers
    #

    def serve(
        self,
        command,
        name="default",
    ):
        if name in self.servers:
            process = self.servers[
                name
            ]

            if process.poll() is None:
                return (
                    f"Server {name!r} is already running "
                    f"with PID {process.pid}"
                )

            del self.servers[
                name
            ]

        arguments = shlex.split(
            command
        )

        if not arguments:
            raise ValueError(
                "Empty command."
            )

        log_path = (
            self.mad
            / f"server-{self._safe_name(name)}.log"
        )

        log_file = log_path.open(
            "a",
            encoding="utf-8",
        )

        process = subprocess.Popen(
            arguments,
            cwd=self.root,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        self.servers[
            name
        ] = process

        return (
            f"Started server {name!r} "
            f"with PID {process.pid}. "
            f"Log: {log_path.relative_to(self.root)}"
        )

    def stop_server(
        self,
        name,
    ):
        process = self.servers.get(
            name
        )

        if process is None:
            return (
                f"No server named {name!r} is running."
            )

        if process.poll() is None:
            process.terminate()

            try:
                process.wait(
                    timeout=5
                )

            except subprocess.TimeoutExpired:
                process.kill()

        del self.servers[
            name
        ]

        return (
            f"Stopped server {name!r}."
        )

    def stop_servers(self):
        names = list(
            self.servers.keys()
        )

        results = []

        for name in names:
            results.append(
                self.stop_server(
                    name
                )
            )

        return results

    #
    # Browser validation
    #

    def _find_free_port(self):
        with contextlib.closing(
            socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM,
            )
        ) as sock:
            sock.bind(
                ("127.0.0.1", 0)
            )

            return sock.getsockname()[1]

    def browser_check(
        self,
        relative_path="index.html",
        wait_ms=1500,
        structured=False,
    ):
        target = self._resolve_agent_path(
            relative_path
        )

        if not target.exists():
            raise FileNotFoundError(
                f"Browser target does not exist: "
                f"{relative_path}"
            )

        if not target.is_file():
            raise ValueError(
                f"Browser target is not a file: "
                f"{relative_path}"
            )

        try:
            from playwright.sync_api import (
                sync_playwright,
            )

        except ImportError as error:
            raise RuntimeError(
                "Playwright is not installed. "
                "Run: pip install playwright && "
                "playwright install chromium"
            ) from error

        port = self._find_free_port()

        class QuietHandler(
            http.server.SimpleHTTPRequestHandler
        ):
            def log_message(
                self,
                format,
                *args,
            ):
                pass

        def handler(
            *args,
            **kwargs,
        ):
            return QuietHandler(
                *args,
                directory=str(self.root),
                **kwargs,
            )

        server = socketserver.TCPServer(
            ("127.0.0.1", port),
            handler,
        )

        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )

        thread.start()

        relative_url = target.relative_to(
            self.root
        ).as_posix()

        url = (
            f"http://127.0.0.1:"
            f"{port}/{relative_url}"
        )

        console_errors = []
        console_warnings = []
        page_errors = []
        request_failures = []

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True
                )

                page = browser.new_page()

                def on_console(message):
                    if message.type == "error":
                        console_errors.append(
                            message.text
                        )

                    elif message.type == "warning":
                        console_warnings.append(
                            message.text
                        )

                page.on(
                    "console",
                    on_console,
                )

                page.on(
                    "pageerror",
                    lambda error: page_errors.append(
                        str(error)
                    ),
                )

                page.on(
                    "requestfailed",
                    lambda request: (
                        request_failures.append(
                            f"{request.method} "
                            f"{request.url}: "
                            f"{request.failure}"
                        )
                    ),
                )

                response = page.goto(
                    url,
                    wait_until="load",
                    timeout=15000,
                )

                page.wait_for_timeout(
                    wait_ms
                )

                title = page.title()

                status_code = (
                    response.status
                    if response
                    else None
                )

                browser.close()

        finally:
            server.shutdown()
            server.server_close()

            thread.join(
                timeout=2
            )

        errors = (
            page_errors
            + console_errors
            + request_failures
        )

        output = [
            f"URL: {url}",
            f"HTTP status: {status_code}",
            f"Title: {title!r}",
            f"Runtime errors: {len(errors)}",
            f"Warnings: {len(console_warnings)}",
        ]

        if errors:
            output.extend(
                [
                    "",
                    "ERRORS:",
                ]
            )

            for error in errors:
                output.append(
                    f"- {error}"
                )

        if console_warnings:
            output.extend(
                [
                    "",
                    "WARNINGS:",
                ]
            )

            for warning in console_warnings:
                output.append(
                    f"- {warning}"
                )

        if errors:
            output.extend(
                [
                    "",
                    "RESULT: FAIL",
                    "Browser/runtime errors were detected.",
                ]
            )

        else:
            output.extend(
                [
                    "",
                    "RESULT: PASS",
                    (
                        "Page loaded without detected "
                        "JavaScript runtime errors."
                    ),
                ]
            )

        text = "\n".join(output)
        if structured:
            return ToolResult(not errors, text)
        return text

    #
    # Tool dispatcher
    #

    def execute_tool(
        self,
        tool,
        path="",
        content="",
        command="",
        name="",
        wait_ms=1500,
    ):
        if tool == "list":
            return self.list_files(
                path or "."
            )

        if tool == "read":
            return self.read(
                path
            )

        if tool == "write":
            return self.write(
                path,
                content,
            )

        if tool == "append":
            return self.append(
                path,
                content,
            )

        if tool == "run":
            return self.run(
                command
            )

        if tool == "serve":
            return self.serve(
                command=command,
                name=name or "default",
            )

        if tool == "stop_server":
            return self.stop_server(
                name=name or "default"
            )

        if tool == "browser_check":
            return self.browser_check(
                relative_path=(
                    path or "index.html"
                ),
                wait_ms=wait_ms,
            )

        raise ValueError(
            f"Unknown workspace tool: {tool}"
        )

    def execute_tool_result(self, tool, **arguments):
        """Structured outcome for sessions; execute_tool retains its text API."""
        if tool == "run":
            return self.run(arguments.get("command", ""), structured=True)
        if tool == "browser_check":
            return self.browser_check(
                arguments.get("path") or "index.html",
                wait_ms=arguments.get("wait_ms", 1500),
                structured=True,
            )
        return ToolResult(True, self.execute_tool(tool, **arguments))

    def write_task_result(self, task, agent_name, status, summary, output):
        """Compatibility entry point; reports are owned by AuditStore."""
        return self.audit_store.write_task_result(task, agent_name, status, summary, output)
