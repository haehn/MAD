from pathlib import Path
import re
import shlex
import subprocess


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

    def _is_ignored(self, path):
        return any(
            part in self.IGNORED_NAMES
            for part in path.parts
        )

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

        #
        # Shallow listing only.
        #
        # This prevents enormous responses from things like
        # node_modules or large repositories.
        #

        for item in sorted(
            path.iterdir(),
            key=lambda p: p.name.lower(),
        ):
            if item.name in self.IGNORED_NAMES:
                if item.is_dir():
                    results.append(
                        f"{item.name}/"
                    )
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
    # Terminating command
    #

    def run(
        self,
        command,
        timeout=120,
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

        return text

    #
    # Long-running server
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
    # Tool dispatcher
    #

    def execute_tool(
        self,
        tool,
        path="",
        content="",
        command="",
        name="",
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

        raise ValueError(
            f"Unknown workspace tool: {tool}"
        )

    #
    # Audit
    #

    def write_task_result(
        self,
        task,
        agent_name,
        status,
        summary,
        output,
    ):
        task_id = self._safe_name(
            task.id
        )

        path = (
            self.audit
            / f"{task_id}.md"
        )

        content = [
            f"# {task.id}: {task.title}",
            "",
            f"**Agent:** {agent_name}",
            f"**Status:** {status}",
            "",
        ]

        if summary:
            content.extend(
                [
                    "## Summary",
                    "",
                    summary,
                    "",
                ]
            )

        if output:
            content.extend(
                [
                    "## Output",
                    "",
                    output,
                    "",
                ]
            )

        path.write_text(
            "\n".join(content),
            encoding="utf-8",
        )

        return path.relative_to(
            self.root
        )
