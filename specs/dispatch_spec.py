#!/usr/bin/env python3
"""
Spec for qd/server.py -- threaded MCP dispatch (LLD "server.py", HLD §6).

Claude-authored gate (never delegate this file -- it defines what correct means).

Drives the dispatch as a REAL subprocess over pipes, with stub handlers that
record start/end timestamps to files -- so every concurrency claim below is
measured wall-clock behavior, not inference. Load-bearing:

  1. Concurrent tools/call requests run in parallel; every stdout line is one
     complete JSON response (the write lock -- a torn line corrupts every
     result on the connection).
  2. Same-repo in-tree delegations SERIALIZE (one actor per tree,
     structurally); different repos overlap.
  3. Per-endpoint semaphores: capacity 1 serializes its profiles' calls
     (KV-cache-correct on a single Ollama slot), capacity 2 overlaps them;
     endpoints never throttle each other. Queries gate on the endpoint too.
  4. A handler exception answers THAT call with an isError receipt and the
     server stays alive.
  5. EOF drains in-flight work before exit (bounded), exit code 0.
  6. Wire protocol is crane-shaped: initialize/tools/list/ping/tools/call
     result wrapping byte-compatible with v1.

Deliberate deviation from the LLD note: locks/semaphores wrap the WHOLE
handler (including git bookkeeping), not just the executor subprocess --
bookkeeping is milliseconds against minutes of inference, and simpler
hand-written concurrency code wins for the one file gates under-prove.

Run:  python3 specs/dispatch_spec.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LAUNCHER = """#!/usr/bin/env python3
import os, sys, time
sys.path.insert(0, %(root)r)
from qd import server

SDIR = os.environ["SPEC_SDIR"]

def slow(args):
    tag = args.get("tag", "?")
    with open(os.path.join(SDIR, "start_" + tag), "w") as f:
        f.write(repr(time.time()))
    time.sleep(float(args.get("sleep", 1.0)))
    with open(os.path.join(SDIR, "end_" + tag), "w") as f:
        f.write(repr(time.time()))
    return "done " + tag

def boom(args):
    raise RuntimeError("handler exploded")

SCHEMAS = [
    {"name": "slow", "description": "stub", "inputSchema": {"type": "object"}},
    {"name": "qwen_delegate", "description": "stub", "inputSchema": {"type": "object"}},
    {"name": "qwen_query", "description": "stub", "inputSchema": {"type": "object"}},
    {"name": "boom", "description": "stub", "inputSchema": {"type": "object"}},
]
server.main(tools={"slow": slow, "qwen_delegate": slow,
                   "qwen_query": slow, "boom": boom}, schemas=SCHEMAS)
"""


class Server:
    """One live dispatch subprocess; responses collected by a reader thread."""

    def __init__(self, sdir, env_extra=None):
        launcher = os.path.join(sdir, "launch.py")
        with open(launcher, "w") as f:
            f.write(LAUNCHER % {"root": ROOT})
        env = dict(os.environ)
        env["SPEC_SDIR"] = sdir
        env.update(env_extra or {})
        self.proc = subprocess.Popen(
            [sys.executable, launcher], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env)
        self.raw = []
        self.responses = {}
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.send({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
        self.wait(0)

    def _read(self):
        for line in self.proc.stdout:
            with self.lock:
                self.raw.append(line.rstrip("\n"))
                try:
                    msg = json.loads(line)
                    if "id" in msg:
                        self.responses[msg["id"]] = msg
                except json.JSONDecodeError:
                    self.responses.setdefault("_torn", []).append(line)

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def send_raw(self, text):
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def call(self, rid, name, args):
        self.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                   "params": {"name": name, "arguments": args}})

    def wait(self, rid, timeout=15):
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self.lock:
                if rid in self.responses:
                    return self.responses[rid]
            time.sleep(0.01)
        raise AssertionError(f"no response for id {rid}")

    def close(self, timeout=15):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        return self.proc.wait(timeout=timeout)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        self.sdir = tempfile.mkdtemp()
        machine = os.path.join(self.sdir, "executors.json")
        with open(machine, "w") as f:
            json.dump({
                "endpoints": {"e1": {"parallel_max": 1},
                              "e2": {"parallel_max": 2}},
                "profiles": {
                    "p1a": {"argv": ["x", "-p", "{task}"], "endpoint": "e1"},
                    "p1b": {"argv": ["x", "-p", "{task}"], "endpoint": "e1"},
                    "p2a": {"argv": ["x", "-p", "{task}"], "endpoint": "e2"},
                    "p2b": {"argv": ["x", "-p", "{task}"], "endpoint": "e2"},
                }}, f)
        os.environ["QWEN_DELEGATE_EXECUTORS"] = machine
        self.srv = Server(self.sdir,
                          env_extra={"QWEN_DELEGATE_EXECUTORS": machine})
        self.repo_a = tempfile.mkdtemp()
        self.repo_b = tempfile.mkdtemp()

    def tearDown(self):
        try:
            self.srv.proc.kill()
        except Exception:
            pass
        os.environ.clear()
        os.environ.update(self._env)

    def stamp(self, kind, tag):
        with open(os.path.join(self.sdir, f"{kind}_{tag}")) as f:
            return float(f.read())

    def assert_overlap(self, tag_a, tag_b):
        self.assertLess(self.stamp("start", tag_b),
                        self.stamp("end", tag_a) + 0.001,
                        f"{tag_b} did not overlap {tag_a}")

    def assert_serialized(self, first, second):
        self.assertGreaterEqual(self.stamp("start", second),
                                self.stamp("end", first) - 0.05,
                                f"{second} overlapped {first}")


class Protocol(Fixture):
    def test_initialize_and_tools_list_crane_shaped(self):
        r = self.srv.responses[0]["result"]
        self.assertEqual(r["protocolVersion"], "2024-11-05")
        self.assertEqual(r["serverInfo"]["name"], "qwen-delegate")
        self.srv.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = self.srv.wait(1)["result"]["tools"]
        self.assertEqual([t["name"] for t in tools],
                         ["slow", "qwen_delegate", "qwen_query", "boom"])

    def test_call_result_wrapping(self):
        self.srv.call(2, "slow", {"tag": "w", "sleep": 0.05})
        r = self.srv.wait(2)["result"]
        self.assertEqual(r["content"], [{"type": "text", "text": "done w"}])

    def test_unknown_tool_and_method(self):
        self.srv.call(3, "nope", {})
        self.assertEqual(self.srv.wait(3)["error"]["code"], -32602)
        self.srv.send({"jsonrpc": "2.0", "id": 4, "method": "bogus/method"})
        self.assertEqual(self.srv.wait(4)["error"]["code"], -32601)

    def test_malformed_line_skipped_server_alive(self):
        self.srv.send_raw("{this is not json")
        self.srv.send({"jsonrpc": "2.0", "id": 5, "method": "ping"})
        self.assertEqual(self.srv.wait(5)["result"], {})


class Concurrency(Fixture):
    def test_parallel_calls_correct_ids_no_torn_lines(self):
        t0 = time.time()
        for i, tag in enumerate(("a", "b", "c", "d")):
            self.srv.call(10 + i, "slow", {"tag": tag, "sleep": 1.2})
        for i, tag in enumerate(("a", "b", "c", "d")):
            r = self.srv.wait(10 + i)
            self.assertEqual(r["result"]["content"][0]["text"], f"done {tag}")
        wall = time.time() - t0
        self.assertLess(wall, 3.5, f"4x1.2s calls took {wall:.1f}s -- serialized?")
        with self.srv.lock:
            self.assertNotIn("_torn", self.srv.responses)
            for line in self.srv.raw:
                json.loads(line)  # every line parses whole

    def test_same_repo_delegates_serialize_cross_repo_overlap(self):
        self.srv.call(20, "qwen_delegate",
                      {"tag": "r1", "sleep": 1.0, "cwd": self.repo_a,
                       "executor": "p2a"})
        self.srv.call(21, "qwen_delegate",
                      {"tag": "r2", "sleep": 1.0, "cwd": self.repo_a,
                       "executor": "p2b"})
        self.srv.call(22, "qwen_delegate",
                      {"tag": "rx", "sleep": 1.0, "cwd": self.repo_b,
                       "executor": "p2a"})
        for rid in (20, 21, 22):
            self.srv.wait(rid)
        self.assert_serialized("r1", "r2")     # same repo: one actor per tree
        self.assert_overlap("r1", "rx")        # different repo: parallel

    def test_endpoint_caps_independent(self):
        # e1 (cap 1): its two profiles serialize even across repos.
        # e2 (cap 2): its two profiles overlap. e1's queue never blocks e2.
        self.srv.call(30, "qwen_delegate",
                      {"tag": "e1a", "sleep": 1.0, "cwd": self.repo_a,
                       "executor": "p1a"})
        self.srv.call(31, "qwen_delegate",
                      {"tag": "e1b", "sleep": 1.0, "cwd": self.repo_b,
                       "executor": "p1b"})
        self.srv.call(32, "qwen_delegate",
                      {"tag": "e2a", "sleep": 1.0,
                       "cwd": tempfile.mkdtemp(), "executor": "p2a"})
        self.srv.call(33, "qwen_delegate",
                      {"tag": "e2b", "sleep": 1.0,
                       "cwd": tempfile.mkdtemp(), "executor": "p2b"})
        for rid in (30, 31, 32, 33):
            self.srv.wait(rid)
        self.assert_serialized("e1a", "e1b")
        self.assert_overlap("e2a", "e2b")
        self.assert_overlap("e1a", "e2a")      # endpoints independent

    def test_queries_gate_on_endpoint_too(self):
        self.srv.call(40, "qwen_query",
                      {"tag": "q1", "sleep": 0.8, "cwd": self.repo_a,
                       "executor": "p1a"})
        self.srv.call(41, "qwen_query",
                      {"tag": "q2", "sleep": 0.8, "cwd": self.repo_b,
                       "executor": "p1b"})
        self.srv.wait(40)
        self.srv.wait(41)
        self.assert_serialized("q1", "q2")


class Failure(Fixture):
    def test_handler_exception_is_error_receipt_server_alive(self):
        self.srv.call(50, "boom", {})
        r = self.srv.wait(50)["result"]
        self.assertTrue(r.get("isError"))
        self.assertIn("handler exploded", r["content"][0]["text"])
        self.srv.send({"jsonrpc": "2.0", "id": 51, "method": "ping"})
        self.assertEqual(self.srv.wait(51)["result"], {})

    def test_unknown_executor_is_error_receipt_not_crash(self):
        self.srv.call(60, "qwen_delegate",
                      {"tag": "z", "sleep": 0.1, "cwd": self.repo_a,
                       "executor": "no-such-profile"})
        r = self.srv.wait(60)["result"]
        self.assertTrue(r.get("isError"))
        self.assertIn("no-such-profile", r["content"][0]["text"])
        self.srv.send({"jsonrpc": "2.0", "id": 61, "method": "ping"})
        self.assertEqual(self.srv.wait(61)["result"], {})


class Drain(Fixture):
    def test_eof_drains_inflight_then_exit_zero(self):
        self.srv.call(70, "slow", {"tag": "drain", "sleep": 1.0})
        time.sleep(0.15)                     # ensure the call is in flight
        rc = self.srv.close(timeout=15)
        self.assertEqual(rc, 0)
        r = self.srv.wait(70)                # response arrived before exit
        self.assertEqual(r["result"]["content"][0]["text"], "done drain")


if __name__ == "__main__":
    unittest.main(verbosity=1)
