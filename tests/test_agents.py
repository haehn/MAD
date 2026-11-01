import asyncio
import contextlib
from dataclasses import fields
from datetime import datetime
import io
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

import requests
from rich.console import Console

import go_mad
from mad import ui

from mad.agents import Agent, Bus, Intelligence, ResponseTooLong
from mad.runtime import read_project, run, start, work


def speaker(messages):
    return messages[0]["content"].splitlines()[0].removeprefix("You are ").removesuffix(".")


class ContainerTests(unittest.IsolatedAsyncioTestCase):
    def test_agent_contains_only_identity_and_links(self):
        intelligence = Intelligence("http://model/v1", "test")
        messaging = Bus()
        agent = Agent("Alice who writes", intelligence, messaging)
        self.assertEqual([field.name for field in fields(Agent)], ["name", "intelligence", "messaging"])
        self.assertEqual(agent.name, "Alice who writes")
        self.assertIs(agent.intelligence, intelligence)
        self.assertIs(agent.messaging, messaging)

    async def test_bus_delivers_messages_in_order_and_rejects_unknown_names(self):
        bus = Bus()
        bus.register("Alice")
        with self.assertRaises(ValueError):
            bus.register("Alice")
        with self.assertRaises(ValueError):
            await bus.send("Bob", "Missing", "hello")
        await bus.send("Bob", "Alice", "first")
        await bus.send("Carol", "Alice", "second")
        self.assertEqual((await bus.receive("Alice")).text, "first")
        second = await bus.receive("Alice")
        self.assertEqual((second.sender, second.recipient, second.text), ("Carol", "Alice", "second"))

    async def test_intelligence_uses_supplied_conversations_without_owning_them(self):
        intelligence = Intelligence("http://model/v1/", "test")
        messages = [{"role": "system", "content": "Alice's role"}, {"role": "user", "content": "First question"}]
        loop_thread = threading.get_ident()

        def response(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            return Mock(json=lambda: {"choices": [{"message": {"content": "A reply"}, "finish_reason": "stop"}]})

        with patch("mad.agents.requests.post", side_effect=response) as post:
            self.assertEqual(await intelligence.reply(messages), "A reply")
            self.assertEqual(len(messages), 2)
            messages.extend([{"role": "assistant", "content": "A reply"}, {"role": "user", "content": "Follow-up"}])
            await intelligence.reply(messages)
            await intelligence.reply([{"role": "user", "content": "Bob's question"}])
        self.assertEqual(post.call_args_list[0].args[0], "http://model/v1/chat/completions")
        self.assertEqual(post.call_args_list[0].kwargs["json"]["model"], "test")
        self.assertEqual(len(post.call_args_list[0].kwargs["json"]["messages"]), 2)
        self.assertEqual(len(post.call_args_list[1].kwargs["json"]["messages"]), 4)
        self.assertEqual(post.call_args_list[2].kwargs["json"]["messages"], [{"role": "user", "content": "Bob's question"}])

    async def test_json_format_is_a_transport_option(self):
        intelligence = Intelligence("http://model/v1", "test")
        with patch("mad.agents.requests.post") as post:
            post.return_value.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
            await intelligence.reply([])
            await intelligence.reply([], json_mode=True)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["max_tokens"], 4096)
        self.assertNotIn("response_format", post.call_args_list[0].kwargs["json"])
        self.assertEqual(post.call_args_list[1].kwargs["json"]["response_format"], {"type": "json_object"})

    async def test_intelligence_surfaces_failed_or_truncated_responses(self):
        intelligence = Intelligence("http://model/v1", "test")
        with patch("mad.agents.requests.post") as post:
            post.return_value.raise_for_status.side_effect = requests.HTTPError("Server failed")
            with self.assertRaises(requests.HTTPError):
                await intelligence.reply([])
            post.return_value.raise_for_status.side_effect = None
            post.return_value.json.return_value = {"choices": [{"finish_reason": "length"}]}
            with self.assertRaisesRegex(RuntimeError, "token limit"):
                await intelligence.reply([])


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
            with patch("sys.argv", argv), patch("mad.runtime.start", new_callable=AsyncMock) as launch:
                run()
            launch.assert_awaited_once()
            self.assertEqual(launch.await_args.args[0], "Write a report.")
            self.assertEqual(launch.await_args.args[2:], ("./other", "http://model/v1", "override"))
            self.assertEqual(launch.await_args.kwargs["models"], {})

    def test_cli_preserves_project_models_without_override(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "project.md")
            path.write_text("Write a report.\nWORKSPACE:\n./report\nAGENTS:\n- Supervisor (Supervisor.md:file-model)\n")
            with patch("sys.argv", ["runtime.py", str(path)]), patch("mad.runtime.start", new_callable=AsyncMock) as launch:
                run()
            self.assertEqual(launch.await_args.kwargs["models"], {"Supervisor": "file-model"})

    def test_existing_python_configuration_entry_point_still_works(self):
        with patch("sys.argv", ["software.py"]), patch("mad.runtime.start", new_callable=AsyncMock) as launch:
            run("Write a report.", {"Supervisor": "Coordinate."}, "./report")
        self.assertEqual(launch.await_args.args[:3], ("Write a report.", {"Supervisor": "Coordinate."}, "./report"))


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_assigns_work_and_checks_a_generic_deliverable(self):
        scripts = {
            "Supervisor": iter([
                {"to": "Coder", "text": "Write greeting.txt containing hello."},
                {"to": "Tester", "text": "Read greeting.txt and assert it contains hello."},
                {"done": "The greeting was independently checked."},
            ]),
            "Coder": iter([
                {"python": "from pathlib import Path; Path('greeting.txt').write_text('hello')"},
                {"to": "Supervisor", "text": "greeting.txt is ready."}, {"wait": True},
            ]),
            "Tester": iter([
                {"python": "from pathlib import Path; assert Path('greeting.txt').read_text() == 'hello'; print('Verified greeting')"},
                {"to": "Supervisor", "text": "The executed check passed."}, {"wait": True},
            ]),
        }
        calls = []

        async def reply(intelligence, messages, **kwargs):
            name = speaker(messages)
            calls.append((name, messages[-1]["content"]))
            return json.dumps(next(scripts[name]))

        roles = {"Supervisor": "Coordinate.", "Coder": "Write.", "Tester": "Verify."}
        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                summary = await asyncio.wait_for(start("Create and verify a greeting.", roles, root, "http://model/v1", "test"), 3)
            self.assertEqual(Path(root, "greeting.txt").read_text(), "hello")
            self.assertEqual(summary, "The greeting was independently checked.")
            logs = list(Path(root, ".mad/audit").glob("*.jsonl"))
            self.assertEqual(len(logs), 1)
            readable = logs[0].with_suffix(".md").read_text()
            self.assertTrue(readable.startswith("# MAD run"))
            self.assertIn(f"[Full event data]({logs[0].name})", readable)
            self.assertIn("Supervisor → Coder", readable)
            self.assertIn("```python", readable)
            self.assertIn("Verified greeting", readable)
            self.assertIn("Run finished", readable)
            self.assertNotIn("Respond with one JSON object", readable)
            events = [json.loads(line) for line in logs[0].read_text().splitlines()]
            self.assertEqual(events[0]["event"], "run_started")
            self.assertEqual(events[-1]["event"], "run_finished")
            self.assertEqual(events[-1]["summary"], summary)
            coder_delivery = next(i for i, event in enumerate(events) if event["event"] == "message_received"
                                  and event.get("agent") == "Supervisor" and event.get("sender") == "Coder")
            tester_briefing = next(i for i, event in enumerate(events) if event["event"] == "message_sent"
                                   and event.get("agent") == "Supervisor" and event.get("recipient") == "Tester")
            self.assertLess(coder_delivery, tester_briefing)
            self.assertTrue(all(event["run_id"] == logs[0].stem for event in events))
            self.assertTrue(all(datetime.fromisoformat(event["time"]).utcoffset() is not None for event in events))
            requests = [event for event in events if event["event"] == "model_request"]
            self.assertEqual({event["agent"] for event in requests}, set(roles))
            for event in requests:
                self.assertIn(Path(__file__).resolve().parent.parent.joinpath("mad/instructions.md").read_text().strip(),
                              event["messages"][0]["content"])
            first_request = next(event for event in events if event["event"] == "model_request")
            self.assertEqual(len(first_request["messages"]), 2)
            self.assertIn("Create and verify a greeting.", first_request["messages"][0]["content"])
            self.assertTrue(any(event["event"] == "model_response" and '"python"' in event["text"] for event in events))
            self.assertTrue(any(event["event"] == "message_sent" and event["agent"] == "Supervisor" and event["recipient"] == "Coder" for event in events))
            self.assertTrue(any(event["event"] == "message_received" and event["agent"] == "Coder" and event["sender"] == "Supervisor" for event in events))
            self.assertTrue(any(event["event"] == "python_started" and "greeting.txt" in event["code"] for event in events))
            self.assertTrue(any(event["event"] == "python_finished" and "Verified greeting" in event["stdout"] for event in events))
            self.assertTrue(any(name == "Tester" and "Verified greeting" in text for name, text in calls))

    async def test_parsed_project_uses_each_agents_declared_model(self):
        scripts = {
            "Coordinator": iter([{"to": "Writer", "text": "Write greeting.txt."}, {"done": "Saved."}]),
            "Writer": iter([{"python": "from pathlib import Path; Path('greeting.txt').write_text('hello')"},
                            {"to": "Coordinator", "text": "Saved greeting.txt."}, {"wait": True}]),
        }
        seen = set()

        async def reply(intelligence, messages, **kwargs):
            name = speaker(messages)
            seen.add((name, intelligence.model))
            return json.dumps(next(scripts[name]))

        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "project.md")
            path.write_text(f"Write a greeting.\nWORKSPACE:\n{root}\nAGENTS:\n"
                            "- Coordinator (Supervisor.md:planning-model)\n- Writer (Coder.md:writing-model)\n")
            task, roles, workspace, models = read_project(path)
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                summary = await asyncio.wait_for(start(task, roles, workspace, "http://model/v1", "fallback", models=models), 3)
            self.assertEqual(Path(root, "greeting.txt").read_text(), "hello")
            self.assertEqual(summary, "Saved.")
        self.assertEqual(seen, {("Coordinator", "planning-model"), ("Writer", "writing-model")})

    async def test_failed_execution_is_reported_to_the_model(self):
        responses = iter([{"python": "raise ValueError('actual failure')"}, {"done": "The command failed."}])
        prompts = []

        async def reply(intelligence, messages, **kwargs):
            prompts.append(messages[-1]["content"])
            return json.dumps(next(responses))

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                await start("Try the command", {"Supervisor": "Coordinate."}, root, "http://model/v1", "test")
        feedback = json.loads(prompts[1])
        self.assertNotEqual(feedback["returncode"], 0)
        self.assertIn("actual failure", feedback["stderr"])

    async def test_workers_cannot_finish_the_example(self):
        messaging = Bus()
        messaging.register("Coder")
        messaging.register("Supervisor")
        agent = Agent("Coder", Intelligence("http://model/v1", "test"), messaging)
        finished = asyncio.get_running_loop().create_future()
        responses = iter([{"done": "I accept"}, {"to": "Supervisor", "text": "Here are my findings."}, {"wait": True}])
        prompts = []

        async def reply(intelligence, messages, **kwargs):
            prompts.append(messages[-1]["content"])
            return json.dumps(next(responses))

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                actor = asyncio.create_task(work(agent, "You are Coder.", root, "Supervisor", finished))
                try:
                    await messaging.send("Supervisor", "Coder", "Work")
                    report = await asyncio.wait_for(messaging.receive("Supervisor"), 2)
                finally:
                    actor.cancel()
                    await asyncio.gather(actor, return_exceptions=True)
        self.assertIn("only the Supervisor can finish", prompts[1])
        self.assertEqual(report.text, "Here are my findings.")
        self.assertFalse(finished.done())

    async def test_workers_message_only_the_configured_supervisor(self):
        for waiting in (True, False):
            with self.subTest(wait=waiting):
                messaging = Bus()
                for name in ("Lead", "Writer", "Reviewer"):
                    messaging.register(name)
                agent = Agent("Writer", Intelligence("http://model/v1", "test"), messaging)
                finished = asyncio.get_running_loop().create_future()
                responses = iter([{"to": "Reviewer", "text": "Please review.", "wait": waiting},
                                  {"to": "Lead", "text": "Please arrange a review."}])
                prompts = []

                async def reply(intelligence, messages, **kwargs):
                    prompts.append(messages[-1]["content"])
                    return json.dumps(next(responses))

                with tempfile.TemporaryDirectory() as root:
                    with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                        actor = asyncio.create_task(work(agent, "You are Writer.", root, "Lead", finished))
                        try:
                            await messaging.send("Lead", "Writer", "Write and report.")
                            report = await asyncio.wait_for(messaging.receive("Lead"), 2)
                        finally:
                            actor.cancel()
                            await asyncio.gather(actor, return_exceptions=True)
                self.assertIn("Send questions, findings, and reports to Lead", prompts[1])
                self.assertEqual(report.sender, "Writer")
                self.assertEqual(report.text, "Please arrange a review.")
                self.assertTrue(messaging.inboxes["Reviewer"].empty())
                self.assertFalse(finished.done())

    async def test_design_is_delivered_and_forwarded_through_workspace_files(self):
        scripts = {
            "Supervisor": iter([
                {"to": "Architect", "text": "Save the chosen word in design.md."},
                {"to": "Coder", "text": "Read the design and implement greeting.txt.", "files": ["design.md"]},
                {"python": "from pathlib import Path; assert Path('greeting.txt').read_text() == 'cedar'; print(Path('checks.md').read_text())"},
                {"done": "Reviewed implementation and checks."},
            ]),
            "Architect": iter([
                {"python": "from pathlib import Path; Path('design.md').write_text('Use the word cedar.')"},
                {"to": "Supervisor", "text": "Design delivered.", "files": ["././design.md"]},
            ]),
            "Coder": iter([
                {"python": "from pathlib import Path; design = Path('design.md').read_text(); word = design.split()[-1].rstrip('.'); Path('greeting.txt').write_text(word); assert word == 'cedar'; Path('checks.md').write_text('PASS: output matches the design.'); print('Read design: ' + design)"},
                {"to": "Supervisor", "text": "Implementation and checks delivered.", "files": ["greeting.txt", "checks.md"]},
            ]),
        }
        coder_received = []

        async def reply(intelligence, messages, **kwargs):
            name = speaker(messages)
            if name == "Coder":
                coder_received.append(messages[-1]["content"])
            return json.dumps(next(scripts[name]))

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                summary = await asyncio.wait_for(start("Deliver the designed greeting", dict.fromkeys(scripts, "Perform your role."),
                                                       root, "http://model/v1", "test"), 3)
            self.assertEqual(Path(root, "greeting.txt").read_text(), "cedar")
            path = next(Path(root, ".mad/audit").glob("*.jsonl"))
            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertIn("Files:\n- design.md", path.with_suffix(".md").read_text())
        self.assertEqual(summary, "Reviewed implementation and checks.")
        self.assertIn("Files:\n- design.md", coder_received[0])
        self.assertIn("Read design: Use the word cedar.", coder_received[1])
        forwarded = next(e for e in events if e["event"] == "message_sent" and e.get("recipient") == "Coder")
        self.assertEqual(forwarded["files"], ["design.md"])
        design_delivery = next(e for e in events if e["event"] == "message_sent" and e.get("agent") == "Architect")
        self.assertEqual(design_delivery["files"], ["design.md"])

    async def test_invalid_handoff_files_are_rejected_before_delivery(self):
        messaging = Bus()
        for name in ("Supervisor", "Worker"):
            messaging.register(name)
        agent = Agent("Worker", Intelligence("http://model/v1", "test"), messaging)
        finished = asyncio.get_running_loop().create_future()
        invalid_files = ["design.md", [], ["missing.md"], ["empty.md"], ["."], ["../outside.md"],
                         [".mad/audit/note.md"], ["outside-link.md"], ["audit-link.md"], [None]]
        prompts = []
        responses = iter([{"to": "Supervisor", "text": "Delivery", "files": files} for files in invalid_files]
                         + [{"to": "Supervisor", "text": "Actual delivery", "files": ["design.md"]}])

        async def reply(intelligence, messages, **kwargs):
            prompts.append(messages[-1]["content"])
            return json.dumps(next(responses))

        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent, "workspace")
            root.mkdir()
            Path(parent, "outside.md").write_text("Outside workspace")
            Path(root, "design.md").write_text("Concrete design")
            Path(root, "empty.md").touch()
            audit = root / ".mad/audit"
            audit.mkdir(parents=True)
            (audit / "note.md").write_text("Infrastructure log")
            (root / "outside-link.md").symlink_to(Path(parent, "outside.md"))
            (root / "audit-link.md").symlink_to(audit / "note.md")
            log = root / ".mad/audit/test.jsonl"
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                actor = asyncio.create_task(work(agent, "You are Worker.", root, "Supervisor", finished, log))
                try:
                    await messaging.send("Supervisor", "Worker", "Deliver the design file.")
                    delivery = await asyncio.wait_for(messaging.receive("Supervisor"), 2)
                finally:
                    actor.cancel()
                    await asyncio.gather(actor, return_exceptions=True)
            events = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(delivery.text, "Actual delivery\n\nFiles:\n- design.md")
        self.assertEqual(len(prompts), len(invalid_files) + 1)
        self.assertTrue(all("Action failed:" in prompt for prompt in prompts[1:]))
        self.assertEqual(sum(e["event"] == "action_failed" for e in events), len(invalid_files))
        self.assertEqual(sum(e["event"] == "message_sent" for e in events), 1)
        self.assertTrue(messaging.inboxes["Supervisor"].empty())

    async def test_sending_a_message_waits_until_new_input_arrives(self):
        messaging = Bus()
        for name in ("Supervisor", "Coder"):
            messaging.register(name)
        agent = Agent("Supervisor", Intelligence("http://model/v1", "test"), messaging)
        finished = asyncio.get_running_loop().create_future()
        prompts = []

        async def reply(intelligence, messages, **kwargs):
            prompts.append(messages[-1]["content"])
            return json.dumps({"to": "Coder", "text": "Implement and report."} if len(prompts) == 1
                              else {"done": "Reviewed the actual report."})

        with tempfile.TemporaryDirectory() as root:
            log = Path(root, "audit.jsonl")
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                actor = asyncio.create_task(work(agent, "You are Supervisor.", root, "Supervisor", finished, log))
                try:
                    await messaging.send("user", "Supervisor", "Start")
                    message = await asyncio.wait_for(messaging.receive("Coder"), 2)
                    self.assertEqual(message.text, "Implement and report.")
                    self.assertEqual(len(prompts), 1)
                    self.assertFalse(finished.done())
                    events = [json.loads(line) for line in log.read_text().splitlines()]
                    self.assertEqual(events[-1]["event"], "agent_waiting")
                    readable = log.with_suffix(".md").read_text()
                    self.assertIn("Supervisor: Waiting", readable)
                    self.assertIn("Message sent to Coder. Waiting for a new message.", readable)
                    await messaging.send("Coder", "Supervisor", "Work completed; check passed.")
                    await asyncio.wait_for(actor, 2)
                finally:
                    actor.cancel()
                    await asyncio.gather(actor, return_exceptions=True)
        self.assertEqual(len(prompts), 2)
        self.assertIn("Coder: Work completed; check passed.", prompts[1])
        self.assertEqual(finished.result(), "Reviewed the actual report.")

    async def test_invalid_message_wait_flag_does_not_send(self):
        messaging = Bus()
        for name in ("Supervisor", "Coder"):
            messaging.register(name)
        agent = Agent("Supervisor", Intelligence("http://model/v1", "test"), messaging)
        finished = asyncio.get_running_loop().create_future()
        responses = iter([{"to": "Coder", "text": "Work", "wait": "false"}, {"done": "Invalid send rejected."}])
        prompts = []

        async def reply(intelligence, messages, **kwargs):
            prompts.append(messages[-1]["content"])
            return json.dumps(next(responses))

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                await messaging.send("user", "Supervisor", "Start")
                await asyncio.wait_for(work(agent, "You are Supervisor.", root, "Supervisor", finished), 2)
        self.assertTrue(messaging.inboxes["Coder"].empty())
        self.assertIn("Message wait must be true or false", prompts[1])

    async def test_supervisor_reads_new_messages_before_finishing(self):
        messaging = Bus()
        messaging.register("Supervisor")
        agent = Agent("Supervisor", Intelligence("http://model/v1", "test"), messaging)
        finished = asyncio.get_running_loop().create_future()
        prompts = []

        async def reply(intelligence, messages, **kwargs):
            prompts.append(messages[-1]["content"])
            if len(prompts) == 1:
                await messaging.send("Tester", "Supervisor", "A check failed.")
                return json.dumps({"done": "Unreviewed draft"})
            return json.dumps({"done": "The failed check was reviewed."})

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply):
                await messaging.send("user", "Supervisor", "Review")
                await asyncio.wait_for(work(agent, "You are Supervisor.", root, "Supervisor", finished), 2)
        self.assertIn("A check failed.", prompts[1])
        self.assertEqual(finished.result(), "The failed check was reviewed.")

    async def test_supervisor_can_start_two_workers_in_parallel(self):
        started, reports = set(), set()
        release = asyncio.Event()
        active = {"Supervisor": 0, "First": 0, "Second": 0}
        assignments = iter([{"to": "First", "text": "First assignment", "wait": False},
                            {"to": "Second", "text": "Second assignment"}])

        async def reply(intelligence, messages, **kwargs):
            name = speaker(messages)
            text = messages[-1]["content"]
            active[name] += 1
            self.assertEqual(active[name], 1)
            try:
                if name == "Supervisor":
                    assignment = next(assignments, None)
                    if assignment:
                        return json.dumps(assignment)
                    for worker in ("First", "Second"):
                        if f"{worker}: finished" in text:
                            reports.add(worker)
                    return json.dumps({"done": "Both workers reported."} if len(reports) == 2 else {"wait": True})
                if name not in started:
                    started.add(name)
                    if len(started) == 2:
                        release.set()
                    await asyncio.wait_for(release.wait(), 1)
                    return json.dumps({"to": "Supervisor", "text": "finished"})
                return json.dumps({"wait": True})
            finally:
                active[name] -= 1

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                summary = await asyncio.wait_for(start("Compare ideas", dict.fromkeys(active, "Discuss."), root, "http://model/v1", "test"), 3)
        self.assertEqual(summary, "Both workers reported.")
        self.assertEqual(started, {"First", "Second"})

    async def test_worker_recovers_from_truncation_and_reports_to_waiting_supervisor(self):
        attempts = 0
        supervisor_calls = 0

        async def reply(intelligence, messages, **kwargs):
            nonlocal attempts, supervisor_calls
            if speaker(messages) == "Supervisor":
                supervisor_calls += 1
                if supervisor_calls == 1:
                    return json.dumps({"to": "Worker", "text": "Inspect and report."})
                if "Worker: Inspected." in messages[-1]["content"]:
                    return json.dumps({"done": "Worker recovered."})
                return json.dumps({"wait": True})
            attempts += 1
            if attempts == 1:
                raise ResponseTooLong("Model response reached its token limit.")
            if attempts == 2:
                self.assertIn("not executed", messages[-1]["content"])
                self.assertIn("shorter, complete JSON action", messages[-1]["content"])
                return json.dumps({"to": "Supervisor", "text": "Inspected."})
            return json.dumps({"wait": True})

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                summary = await asyncio.wait_for(start("Work", {"Supervisor": "Coordinate.", "Worker": "Inspect."},
                                                       root, "http://model/v1", "test"), 3)
            events = [json.loads(line) for line in next(Path(root, ".mad/audit").glob("*.jsonl")).read_text().splitlines()]
        self.assertEqual(summary, "Worker recovered.")
        self.assertFalse(any(event["event"] == "agent_failed" for event in events))
        self.assertEqual(sum(event["event"] == "action_failed" for event in events), 1)

    async def test_runtime_model_failure_stops_the_other_actors(self):
        async def reply(intelligence, messages, **kwargs):
            raise requests.ConnectionError("Unavailable")

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ExceptionGroup) as errors:
                    await asyncio.wait_for(start("Work", {"Supervisor": "Coordinate."}, root, "http://model/v1", "test"), 2)
            events = [json.loads(line) for line in next(Path(root, ".mad/audit").glob("*.jsonl")).read_text().splitlines()]
            failed = next(event for event in events if event["event"] == "agent_failed")
            self.assertEqual(failed["agent"], "Supervisor")
            self.assertEqual(failed["error_type"], "ConnectionError")
            self.assertIn("Unavailable", failed["traceback"])
            self.assertEqual(events[-1]["event"], "run_failed")
            self.assertIn("Unavailable", events[-1]["traceback"])
        self.assertTrue(any(isinstance(error, requests.ConnectionError) for error in errors.exception.exceptions))


class LoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_responses_errors_code_and_complete_output_are_preserved(self):
        output = "Unicode π and multiple lines\n" + "x" * 12000
        code = f"import sys; print({output!r}); print('actual stderr', file=sys.stderr); raise SystemExit(2)"
        responses = iter(["not JSON", json.dumps({"to": "Supervisor", "text": "self"}),
                          json.dumps({"python": code}), json.dumps({"done": "Finished with observed errors."})])

        async def reply(intelligence, messages, **kwargs):
            return next(responses)

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                await start("Inspect failures", {"Supervisor": "Coordinate."}, root, "http://model/v1", "test")
            events = [json.loads(line) for line in next(Path(root, ".mad/audit").glob("*.jsonl")).read_text().splitlines()]
            readable = next(Path(root, ".mad/audit").glob("*.md")).read_text()
        self.assertIn(output, readable)
        self.assertIn("actual stderr", readable)
        self.assertIn("JSONDecodeError", readable)
        self.assertTrue(any(event["event"] == "model_response" and event["text"] == "not JSON" for event in events))
        failures = [event for event in events if event["event"] == "action_failed"]
        self.assertEqual([event["error_type"] for event in failures], ["JSONDecodeError", "ValueError"])
        self.assertEqual(next(event["code"] for event in events if event["event"] == "python_started"), code)
        execution = next(event for event in events if event["event"] == "python_finished")
        self.assertEqual(execution["returncode"], 2)
        self.assertEqual(execution["stdout"], output + "\n")
        self.assertEqual(execution["stderr"], "actual stderr\n")

    async def test_provider_error_response_is_saved_with_the_failure(self):
        response = requests.Response()
        response.status_code = 400
        response._content = b'{"error":"maximum context length exceeded"}'
        failure = requests.HTTPError("400 Bad Request", response=response)

        async def reply(intelligence, messages, **kwargs):
            raise failure

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ExceptionGroup):
                    await start("Inspect request failure", {"Supervisor": "Coordinate."}, root, "http://model/v1", "test")
            events = [json.loads(line) for line in next(Path(root, ".mad/audit").glob("*.jsonl")).read_text().splitlines()]
        failed = next(event for event in events if event["event"] == "agent_failed")
        self.assertEqual(json.loads(failed["response_body"]), {"error": "maximum context length exceeded"})

    async def test_truncated_reply_is_preserved_in_the_error_trace(self):
        partial = '{"python":"unfinished code'
        with tempfile.TemporaryDirectory() as root:
            with patch("mad.agents.requests.post") as post, contextlib.redirect_stdout(io.StringIO()):
                post.return_value.json.return_value = {"choices": [{"finish_reason": "length", "message": {"content": partial}}]}
                with self.assertRaises(ExceptionGroup):
                    await start("Inspect truncation", {"Supervisor": "Coordinate."}, root, "http://model/v1", "test")
            events = [json.loads(line) for line in next(Path(root, ".mad/audit").glob("*.jsonl")).read_text().splitlines()]
        failed = next(event for event in events if event["event"] == "agent_failed")
        self.assertEqual(post.call_count, 3)
        retries = [event for event in events if event["event"] == "action_failed"]
        self.assertEqual(len(retries), 3)
        self.assertTrue(all(partial in event["traceback"] for event in retries))
        self.assertFalse(any(event["event"] == "python_started" for event in events))
        self.assertIn(partial, failed["traceback"])
        self.assertEqual(events[-1]["event"], "run_failed")

    async def test_each_run_keeps_a_separate_log(self):
        async def reply(intelligence, messages, **kwargs):
            return json.dumps({"done": "Finished."})

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                await start("First run", {"Supervisor": "Coordinate."}, root, "http://model/v1", "test")
                first = next(Path(root, ".mad/audit").glob("*.jsonl"))
                original = first.read_text()
                original_markdown = first.with_suffix(".md").read_text()
                await start("Second run", {"Supervisor": "Coordinate."}, root, "http://model/v1", "test")
            self.assertEqual(len(list(Path(root, ".mad/audit").glob("*.jsonl"))), 2)
            self.assertEqual(first.read_text(), original)
            self.assertEqual(first.with_suffix(".md").read_text(), original_markdown)
            self.assertEqual(len(list(Path(root, ".mad/audit").glob("*.md"))), 2)

    async def test_logs_are_flushed_during_work_and_preserved_on_cancellation(self):
        entered = asyncio.Event()

        async def reply(intelligence, messages, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, "reply", reply), contextlib.redirect_stdout(io.StringIO()):
                running = asyncio.create_task(start("Wait for reply", {"Supervisor": "Coordinate."}, root, "http://model/v1", "test"))
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    path = next(Path(root, ".mad/audit").glob("*.jsonl"))
                    live = [json.loads(line) for line in path.read_text().splitlines()]
                    self.assertEqual(live[-1]["event"], "model_request")
                    self.assertIn("Wait for reply", path.with_suffix(".md").read_text())
                finally:
                    running.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await running
            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertIn("run cancelled", path.with_suffix(".md").read_text())
        self.assertEqual(events[-1]["event"], "run_cancelled")
        self.assertTrue(any(event["event"] == "agent_stopped" for event in events))
        self.assertFalse(any(event["event"] == "run_finished" for event in events))


class UITests(unittest.TestCase):
    def test_banner_messages_and_results_are_colored_and_keep_model_text_literal(self):
        output = io.StringIO()
        console = Console(file=output, force_terminal=True, color_system="standard", width=120)
        agent = Agent("Lead [red]", Intelligence("http://model/v1", "model[blue]"), Bus())
        with patch.object(ui, "console", console):
            ui.start("Task [bold]", "./workspace", [agent], log_path="./workspace/.mad/audit/run.md")
            ui.activity(agent.name)
            ui.message(agent.name, "Worker", "Message [green]")
            ui.tool_result(agent.name, 0, "Check passed [yellow]", "")
            ui.tool_result(agent.name, 1, "", "Error [cyan]")
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
            with patch("sys.argv", ["go_mad.py", str(path)]), patch("mad.runtime.start", new_callable=AsyncMock) as launch:
                self.assertEqual(go_mad.main(), 0)
            launch.assert_awaited_once()
            self.assertEqual(launch.await_args.args[:3], ("Write a report.", {"Supervisor": "Supervisor"}, "./report"))

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


if __name__ == "__main__":
    unittest.main()
