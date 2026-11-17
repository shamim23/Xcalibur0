"""CLI tests. The provider is stubbed, so nothing here touches the network."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from xcalibur import cli, commands, provider, session
from xcalibur.harness import Harness
from xcalibur.security import Policy
from xcalibur.ui import Console


def reply(text="", calls=()):
    return {"text": text, "usage": {"input": 10, "output": 5},
            "tool_calls": [{"name": n, "args": a, "signature": "SECRET"} for n, a in calls]}


class FakeStdin(io.StringIO):
    def __init__(self, text="", tty=False):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = self.tmp.name
        self.replies = []
        self.seen = []

        def fake_complete(model, system, messages, tools=None):
            self.seen.append([dict(m) for m in messages])
            return self.replies.pop(0) if self.replies else reply("done")
        patcher = mock.patch.object(provider, "complete", side_effect=fake_complete)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", FakeStdin(stdin)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()


class HeadlessTests(CliTestCase):
    def test_text_prints_only_the_answer(self):
        self.replies = [reply("hello there")]
        code, out, _ = self.run_cli(["-p", "hi", "-d", self.workdir])
        self.assertEqual(code, 0)
        self.assertEqual(out, "hello there\n")

    def test_json_output_shape(self):
        self.replies = [reply("", [("list_files", {})]), reply("final")]
        code, out, _ = self.run_cli(["-p", "go", "-d", self.workdir,
                                     "--output-format", "json"])
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"], "final")
        self.assertEqual(data["turns"], 2)
        self.assertEqual(data["usage"], {"input": 20, "output": 10})
        self.assertTrue(os.path.isfile(data["session"]))

    def test_stream_json_events_and_no_signature(self):
        self.replies = [reply("", [("list_files", {})]), reply("final")]
        code, out, _ = self.run_cli(["-p", "go", "-d", self.workdir,
                                     "--output-format", "stream-json"])
        lines = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([l["type"] for l in lines],
                         ["assistant", "tool_start", "tool_end", "assistant", "result"])
        self.assertNotIn("SECRET", out)
        self.assertEqual(lines[-1]["result"], "final")

    def test_piped_stdin_is_appended_and_implies_headless(self):
        code, out, _ = self.run_cli(["-d", self.workdir, "explain"], stdin="log line\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.seen[0][0]["text"], "explain\n\nlog line")

    def test_provider_error_exits_1_with_json_error(self):
        with mock.patch.object(provider, "complete", side_effect=RuntimeError("HTTP 401")):
            code, out, _ = self.run_cli(["-p", "x", "-d", self.workdir,
                                         "--output-format", "json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error"], "HTTP 401")

    def test_usage_error_exits_2(self):
        code, _, _ = self.run_cli(["--mode", "bogus"])
        self.assertEqual(code, 2)

    def test_continue_resumes_latest_session(self):
        self.run_cli(["-p", "first task", "-d", self.workdir])
        self.run_cli(["-p", "second", "-d", self.workdir, "-c"])
        self.assertEqual(len(session.list_sessions(self.workdir)), 1)
        self.assertEqual(self.seen[-1][0]["text"], "first task")

    def test_resume_unknown_id_fails(self):
        code, _, err = self.run_cli(["-p", "x", "-d", self.workdir, "--resume", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("no session matches", err)


class ApprovalTests(CliTestCase):
    def test_always_approves_rest_of_session(self):
        answers = iter(["a"])
        console = Console(out=io.StringIO(), err=io.StringIO(), ask=lambda p: next(answers))
        policy = Policy("safe", console.approve)
        call = {"name": "bash", "args": {"command": "echo hi"}}
        self.assertIsNone(policy.check(call))
        self.assertIsNone(policy.check(call))  # no second prompt: iterator would raise

    def test_deny_list_beats_always(self):
        console = Console(out=io.StringIO(), err=io.StringIO(), ask=lambda p: "y")
        console.always.add("bash")
        policy = Policy("safe", console.approve)
        self.assertIsNotNone(policy.check({"name": "bash", "args": {"command": "sudo ls"}}))

    def test_eof_is_a_no(self):
        def eof(prompt):
            raise EOFError
        console = Console(out=io.StringIO(), err=io.StringIO(), ask=eof)
        policy = Policy("safe", console.approve)
        self.assertIsNotNone(policy.check({"name": "write_file", "args": {}}))


class SlashCommandTests(CliTestCase):
    def make_repl(self):
        out = io.StringIO()
        console = Console(out=out, err=out)
        harness = Harness(self.workdir, policy=Policy("safe"), on_event=console.on_event)
        repl = cli.Repl.__new__(cli.Repl)
        repl.harness, repl.console, repl._readline = harness, console, False
        return repl, out

    def test_mode_model_and_unknown(self):
        repl, out = self.make_repl()
        commands.dispatch(repl, "/mode yolo")
        commands.dispatch(repl, "/model other-model")
        commands.dispatch(repl, "/nope")
        self.assertEqual(repl.harness.policy.mode, "yolo")
        self.assertEqual(repl.harness.model, "other-model")
        self.assertIn("unknown command", out.getvalue())

    def test_clear_and_exit(self):
        repl, _ = self.make_repl()
        repl.harness.run("hello")
        self.assertTrue(repl.harness.messages)
        commands.dispatch(repl, "/clear")
        self.assertEqual(repl.harness.messages, [])
        self.assertIsNone(repl.harness.session_path)
        self.assertEqual(commands.dispatch(repl, "/exit"), "exit")

    def test_compact_shrinks_transcript(self):
        repl, _ = self.make_repl()
        repl.harness.messages = [{"role": "user", "text": "m%d" % i} for i in range(12)]
        self.replies = [reply("summary")]
        commands.dispatch(repl, "/compact")
        self.assertEqual(len(repl.harness.messages), 7)
        self.assertIn("summary", repl.harness.messages[0]["text"])

    def test_remember_updates_system_prompt(self):
        repl, _ = self.make_repl()
        commands.dispatch(repl, "/remember tests run with unittest")
        self.assertIn("tests run with unittest", repl.harness.system)

    def test_prefix_and_completion(self):
        repl, _ = self.make_repl()
        self.assertEqual(commands.dispatch(repl, "/exi"), "exit")
        self.assertEqual(commands.complete("/com", 0), "/compact")


class SubcommandTests(CliTestCase):
    def test_sessions_list_and_show(self):
        self.run_cli(["-p", "build a thing", "-d", self.workdir])
        code, out, _ = self.run_cli(["sessions", "-d", self.workdir, "--json"])
        items = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(items[0]["title"], "build a thing")
        code, out, _ = self.run_cli(["sessions", "show", items[0]["id"][:6],
                                     "-d", self.workdir, "--no-color"])
        self.assertIn("build a thing", out)

    def test_fleet_runs_jobs(self):
        jobs = os.path.join(self.workdir, "jobs.json")
        with open(jobs, "w") as handle:
            json.dump([{"name": "a", "workdir": "a", "task": "t1"},
                       {"name": "b", "workdir": "b", "task": "t2"}], handle)
        code, out, _ = self.run_cli(["fleet", jobs, "--json"])
        results = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([r["name"] for r in results], ["a", "b"])
        self.assertTrue(os.path.isdir(os.path.join(self.workdir, "a")))

    def test_doctor_without_key_fails(self):
        with mock.patch.object(provider, "api_key", side_effect=RuntimeError("No API key")):
            code, out, _ = self.run_cli(["doctor", "-d", self.workdir, "--no-color"])
        self.assertEqual(code, 1)
        self.assertIn("No API key", out)


if __name__ == "__main__":
    unittest.main()
