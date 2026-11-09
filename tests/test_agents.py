import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
from rich.console import Console

import go_mad
from mad import ui
from mad.runtime import read_project, run


class ProjectParsingTests(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "project.md")
            path.write_text(text)
            return read_project(path)

    def test_short_agent_entries_need_no_descriptions_or_templates(self):
        task, roles, workspace, models = self.read(
            "Write a short report.\n\nWORKSPACE:\n./report\n\nAGENTS:\n- Coordinator\n- Writer\n- Reviewer\n")
        self.assertEqual(task, "Write a short report.")
        self.assertEqual(roles, {"Coordinator": "Coordinator", "Writer": "Writer", "Reviewer": "Reviewer"})
        self.assertEqual(workspace, "./report")
        self.assertEqual(models, {})

    def test_existing_templates_models_descriptions_and_success_are_preserved(self):
        task, roles, workspace, models = self.read(
            "Compare the supplied sources.\nWORKSPACE:\n./analysis\nSUCCESS:\n- Cite the sources.\n"
            "AGENTS:\n- Lead who coordinates (Supervisor.md:planning-model)\n"
            "- Researcher who compares evidence (Researcher.md:research-model)\n")
        self.assertIn("Cite the sources.", task)
        template = Path(__file__).resolve().parent.parent / "mad_agents/core/Supervisor.md"
        self.assertEqual(roles["Lead"], template.read_text().strip() + "\ncoordinates")
        self.assertIn("coordinates", roles["Lead"])
        self.assertIn("compares evidence", roles["Researcher"])
        self.assertNotIn("skills:", roles["Researcher"])
        self.assertEqual(models, {"Lead": "planning-model", "Researcher": "research-model"})
        self.assertEqual(workspace, "./analysis")

    def test_current_repository_examples_can_be_parsed(self):
        root = Path(__file__).resolve().parent.parent
        for path in sorted((root / "examples").glob("*.md")):
            with self.subTest(path=path.name):
                task, roles, workspace, models = read_project(path)
                self.assertTrue(task)
                self.assertTrue(workspace)
                self.assertTrue(roles)
                self.assertEqual(set(models), set(roles))

    def test_invalid_or_incomplete_projects_fail_clearly(self):
        cases = [
            "WORKSPACE:\n./out\nAGENTS:\n- Supervisor\n",
            "Task\nAGENTS:\n- Supervisor\n",
            "Task\nWORKSPACE:\n./out\n",
            "Task\nWORKSPACE:\n./out\nAGENTS:\n- Supervisor\n- Supervisor\n",
            "Task\nWORKSPACE:\n./out\nAGENTS:\n- \n",
            "Task\nWORKSPACE:\n./out\nAGENTS:\n- Supervisor (Supervisor.md:)\n",
            "Task\nWORKSPACE:\n./out\nAGENTS:\n- Supervisor (Missing.md:model)\n",
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.read(text)

    def test_cli_reads_project_and_applies_explicit_overrides(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "project.md")
            path.write_text("Write a report.\nWORKSPACE:\n./report\nAGENTS:\n- Supervisor (Supervisor.md:file-model)\n")
            argv = ["runtime.py", str(path), "--server", "http://model/v1", "--model", "override", "--workspace", "./other"]
            with patch("sys.argv", argv), patch("mad.runtime.start", new_callable=Mock) as launch:
                run()
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[0], "Write a report.")
            self.assertEqual(launch.call_args.args[2:], ("./other", "http://model/v1", "override"))
            self.assertEqual(launch.call_args.kwargs["models"], {})

    def test_cli_preserves_project_models_without_override(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "project.md")
            path.write_text("Write a report.\nWORKSPACE:\n./report\nAGENTS:\n- Supervisor (Supervisor.md:file-model)\n")
            with patch("sys.argv", ["runtime.py", str(path)]), patch("mad.runtime.start", new_callable=Mock) as launch:
                run()
            self.assertEqual(launch.call_args.kwargs["models"], {"Supervisor": "file-model"})

    def test_existing_python_configuration_entry_point_still_works(self):
        with patch("sys.argv", ["software.py"]), patch("mad.runtime.start", new_callable=Mock) as launch:
            run("Write a report.", {"Supervisor": "Coordinate."}, "./report")
        self.assertEqual(launch.call_args.args[:3], ("Write a report.", {"Supervisor": "Coordinate."}, "./report"))


class UITests(unittest.TestCase):
    def test_banner_messages_and_results_are_colored_and_keep_model_text_literal(self):
        output = io.StringIO()
        console = Console(file=output, force_terminal=True, color_system="standard", width=120)
        agent = SimpleNamespace(role="Lead [red]", llm=SimpleNamespace(model="model[blue]"))
        with patch.object(ui, "console", console):
            ui.start("Task [bold]", "./workspace", [agent], log_path="./workspace/.mad/audit/run.md")
            ui.activity(agent.role)
            ui.message(agent.role, "Worker", "Message [green]")
            ui.tool_result(agent.role, 0, "Check passed [yellow]", "")
            ui.tool_result(agent.role, 1, "", "Error [cyan]")
            ui.finish("Result [magenta]")
            ui.error("Failure [blue]")
        text = output.getvalue()
        self.assertIn("\x1b[", text)
        for expected in ["MULTI AGENT DRIVER", "Task [bold]", "Lead [red]", "model[blue]", "Message [green]",
                         "Python exited 0", "Python exited 1", "Check passed [yellow]", "Error [cyan]",
                         "Result [magenta]", "Failure [blue]", "Log: ./workspace/.mad/audit/run.md"]:
            self.assertIn(expected, text)
        self.assertNotIn("Project accepted", text)


class MarkdownLogTests(unittest.TestCase):
    def test_waiting_is_explicit_for_sent_messages_and_wait_actions(self):
        base = {"event": "agent_waiting", "time": "2026-11-01T01:00:00+00:00", "agent": "Supervisor"}
        for details in ({}, {"reason": "Message sent"}, {"reason": "Message sent", "recipient": "Coder"}):
            with self.subTest(details=details):
                text = ui.log_entry({**base, **details})
                self.assertIn("Supervisor: Waiting", text)
                self.assertIn("Waiting for a new message.", text)
                if "recipient" in details:
                    self.assertIn("Message sent to Coder.", text)

    def test_python_with_embedded_markdown_fences_stays_in_one_code_block(self):
        code = "print('```')\nprint('hello')"
        entry = {"event": "python_started", "time": "2026-11-01T01:00:00+00:00", "agent": "Writer", "code": code}
        text = ui.log_entry(entry)
        self.assertIn("## 2026-11-01 01:00:00 UTC — Writer: Python execution", text)
        self.assertIn("````python\n" + code + "\n````", text)

    def test_errors_include_provider_response_and_traceback(self):
        entry = {"event": "agent_failed", "time": "2026-11-01T01:00:00+00:00", "agent": "Writer",
                 "error_type": "HTTPError", "error": "400 Bad Request", "response_body": "Context limit exceeded",
                 "traceback": "Traceback details"}
        text = ui.log_entry(entry)
        self.assertIn("### Provider response", text)
        self.assertIn("Context limit exceeded", text)
        self.assertIn("### Traceback", text)
        self.assertIn("Traceback details", text)


class DriverTests(unittest.TestCase):
    def test_driver_imports_package_from_another_working_directory(self):
        driver = Path(__file__).resolve().parent.parent / "go_mad.py"
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, str(driver), "--help"], cwd=root,
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--server", result.stdout)
        self.assertIn("--workspace", result.stdout)

    def test_driver_loads_the_markdown_project(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "project.md")
            path.write_text("Write a report.\nWORKSPACE:\n./report\nAGENTS:\n- Supervisor\n")
            with patch("sys.argv", ["go_mad.py", str(path)]), patch("mad.runtime.start", new_callable=Mock) as launch:
                self.assertEqual(go_mad.main(), 0)
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[:3], ("Write a report.", {"Supervisor": "Supervisor"}, "./report"))

    def test_driver_reports_model_failure_and_returns_nonzero(self):
        failure = requests.ConnectionError("Model unavailable")
        with patch.object(go_mad, "run", side_effect=ExceptionGroup("Actor failed", [failure])):
            with patch.object(ui, "error") as show_error:
                self.assertEqual(go_mad.main(), 1)
        show_error.assert_called_once_with(failure)

    def test_driver_handles_interrupt_without_a_traceback(self):
        with patch.object(go_mad, "run", side_effect=KeyboardInterrupt), patch.object(ui, "error") as show_error:
            self.assertEqual(go_mad.main(), 130)
        show_error.assert_called_once_with("Stopped.")

    def test_driver_keeps_argument_errors_as_exit_code_two(self):
        with patch("sys.argv", ["go_mad.py"]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                go_mad.main()
        self.assertEqual(error.exception.code, 2)
