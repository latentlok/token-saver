#!/usr/bin/env python3
"""
Spec for the executor endpoint health probe (qd/probe.py and its three hooks).

Claude-authored gate (never delegate this file -- it defines what correct means).

Field evidence, 2026-08-07: the executor endpoint (vLLM behind an auth proxy)
was hard down -- HTTP 502 in ~8ms with valid auth -- and nothing checked
reachability before spawning workers. Two delegation runs burned ~380s and 11
api_errors each, produced 0 tokens, and both filed `gate_suspect` because the
gate was byte-identical before and after a worker that never wrote anything.
A sub-second GET would have turned that into an immediate, labelled refusal.

Load-bearing claims pinned here:

  1. Verdict semantics: DOWN is only what is PROVABLE -- connection error,
     timeout, or HTTP >= 500. A server that answers at all (2xx, 401, 403,
     404) is up; auth is the worker's business. No base URL discoverable on
     the profile means NO probe -- no behavior change for configurations the
     probe cannot see (cloud-default executors, the builtin qwen-local).
  2. The refusal reuses the existing machinery (status `refused`, like GATE
     UNUSABLE) with a named marker: `EXECUTOR UNREACHABLE: ...`. It fires
     BEFORE the first executor call -- the stub worker must never have been
     invoked on a refused run.
  3. Once per CALL, not per item: a chain or batch probes each DISTINCT
     endpoint exactly once at the head, and links/items inherit the verdict
     through engine.PROBED_ARG instead of re-probing. Verdicts are never
     cached ACROSS calls: a just-restarted endpoint works on the next call.
  4. query refuses the same way (it is synchronous, so a dead endpoint
     used to cost the whole executor timeout inline).
  5. doctor.project_check reports a down endpoint ("endpoint-down"), so the
     static config sweep catches what the run-time refusal catches.

Public surface pinned here:
    qd.probe.base_url(profile) -> (url|None, env_key|None)
    qd.probe.down(profile, timeout=..) -> None | {endpoint, url, signal, ms}
    qd.probe.refusal(profile, timeout=..) -> None | "EXECUTOR UNREACHABLE: .."
    qd.engine.PROBED_ARG; the probe in the delegate path
    qd.server._endpoint_refusal + the run_delegate_batch hook
    qd.queries.run_query refusal; qd.doctor.project_check "endpoint-down"

Run:  python3 specs/probe_spec.py
"""

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qd import probe  # noqa: E402


# --- HTTP fixture: a real local server, one canned status code ---------------
# A real socket rather than a mock: the verdicts under test are exactly the
# transport-level distinctions (connection refused vs 502 vs timeout) that a
# mocked urllib cannot produce honestly.

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        srv = self.server
        srv.hits += 1
        srv.paths.append(self.path)
        srv.auth.append(self.headers.get("Authorization"))
        if srv.delay:
            time.sleep(srv.delay)
        try:
            self.send_response(srv.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")
        except (BrokenPipeError, ConnectionResetError):
            # The timeout test hangs up before we answer; that is the point.
            pass

    def log_message(self, *args):
        pass


def serve(code=200, delay=0.0):
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.code = code
    srv.delay = delay
    srv.hits = 0
    srv.paths = []
    srv.auth = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def url_of(srv):
    return "http://127.0.0.1:%d/v1" % srv.server_address[1]


def closed_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "http://127.0.0.1:%d/v1" % port


def make_profile(url=None, env_key=None, name="probe-ep", env=None):
    """The subset of a resolved profile the probe reads, in the machine-file
    shape (settings_overlay.modelProviders.<provider>[0].baseUrl + envKey)."""
    p = {"name": name, "endpoint": name, "env": env or {},
         "settings_overlay": None}
    if url:
        entry = {"id": "m", "baseUrl": url}
        if env_key:
            entry["envKey"] = env_key
        p["settings_overlay"] = {"modelProviders": {"openai": [entry]}}
    return p


class Verdicts(unittest.TestCase):
    """Claim 1: DOWN is only what is provable; an answer is an answer."""

    def setUp(self):
        self._env = dict(os.environ)
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.shutdown()
            srv.server_close()
        os.environ.clear()
        os.environ.update(self._env)

    def serve(self, **kw):
        srv = serve(**kw)
        self.servers.append(srv)
        return srv

    def test_http_5xx_is_down(self):
        srv = self.serve(code=502)
        text = probe.refusal(make_profile(url_of(srv)))
        self.assertIsNotNone(text)
        self.assertTrue(text.startswith("EXECUTOR UNREACHABLE:"), text)
        self.assertIn("HTTP 502", text)
        self.assertIn(url_of(srv).rstrip("/") + "/models", text)
        self.assertIn("probe-ep", text)
        self.assertIn("nothing was run", text)

    def test_2xx_is_alive(self):
        srv = self.serve(code=200)
        self.assertIsNone(probe.refusal(make_profile(url_of(srv))))
        self.assertEqual(srv.hits, 1)

    def test_401_is_alive(self):
        # The field endpoint answered 401 without auth while 502ing with it;
        # an auth refusal still proves a server is there to refuse.
        srv = self.serve(code=401)
        self.assertIsNone(probe.refusal(make_profile(url_of(srv))))

    def test_404_is_alive(self):
        srv = self.serve(code=404)
        self.assertIsNone(probe.refusal(make_profile(url_of(srv))))

    def test_closed_port_is_down(self):
        text = probe.refusal(make_profile(closed_port_url()))
        self.assertIsNotNone(text)
        self.assertTrue(text.startswith("EXECUTOR UNREACHABLE:"), text)

    def test_timeout_is_down(self):
        srv = self.serve(code=200, delay=1.0)
        text = probe.refusal(make_profile(url_of(srv)), timeout=0.25)
        self.assertIsNotNone(text)
        self.assertIn("timed out", text)

    def test_no_base_url_skips_the_probe_entirely(self):
        # A profile with no discoverable base URL (builtin qwen-local, a cloud
        # default) must see no behavior change -- and down() must not guess.
        self.assertIsNone(probe.down(make_profile()))
        self.assertIsNone(probe.down({"name": "x", "settings_overlay": {}}))
        self.assertIsNone(probe.refusal(make_profile()))
        url, key = probe.base_url(make_profile())
        self.assertIsNone(url)
        self.assertIsNone(key)

    def test_probes_the_models_route(self):
        # GET <baseUrl>/models -- the OpenAI-compatible liveness route the
        # vLLM cutover round already used by hand (docs/archive VLLM-ROUND A2).
        srv = self.serve(code=200)
        probe.down(make_profile(url_of(srv)))
        self.assertEqual(srv.paths, ["/v1/models"])

    def test_bearer_sent_when_env_key_names_a_set_variable(self):
        srv = self.serve(code=200)
        os.environ["QD_PROBE_SPEC_TOKEN"] = "sekrit"
        probe.down(make_profile(url_of(srv), env_key="QD_PROBE_SPEC_TOKEN"))
        self.assertEqual(srv.auth, ["Bearer sekrit"])

    def test_profile_env_wins_over_process_env(self):
        # The worker's environment is os.environ updated with profile["env"]
        # (qd/invoke.py), so the profile value is what the worker would send.
        srv = self.serve(code=200)
        os.environ["QD_PROBE_SPEC_TOKEN"] = "process"
        probe.down(make_profile(url_of(srv), env_key="QD_PROBE_SPEC_TOKEN",
                                env={"QD_PROBE_SPEC_TOKEN": "profile"}))
        self.assertEqual(srv.auth, ["Bearer profile"])

    def test_no_auth_header_without_env_key(self):
        os.environ.pop("QD_PROBE_SPEC_TOKEN", None)
        srv = self.serve(code=200)
        probe.down(make_profile(url_of(srv)))
        self.assertEqual(srv.auth, [None])


# --- The stub executor: same shape engine_spec drives ------------------------

STUB = r"""#!/usr/bin/env python3
import json, os, sys
sdir = os.environ["STUB_DIR"]
n_path = os.path.join(sdir, "attempt")
n = int(open(n_path).read()) if os.path.exists(n_path) else 0
open(n_path, "w").write(str(n + 1))
open(os.path.join(sdir, "task_%d.txt" % (n + 1)), "w").write(sys.argv[2])
open(os.path.join(os.getcwd(), "out.py"), "w").write("MARKER\n")
result = {"type": "result",
          "result": "did the work\n\nHANDOFF: ok\nFILES: none\nNEXT: nothing",
          "session_id": "p-sess-%d" % (n + 1), "permission_denials": [],
          "stats": {"tools": {"totalCalls": 1, "totalFail": 0, "byName": {}},
                    "models": {}}}
msgs = [{"type": "assistant", "message": {"usage": {"input_tokens": 5000}}},
        result]
if "stream-json" in sys.argv:
    for m in msgs:
        sys.stdout.write(json.dumps(m) + "\n")
        sys.stdout.flush()
else:
    sys.stdout.write(json.dumps(msgs))
"""


class Fixture(unittest.TestCase):
    """A committed git repo + a stub executor whose profile carries a
    settings_overlay base URL pointing at a local fixture server."""

    def setUp(self):
        self._env = dict(os.environ)
        self.td = tempfile.mkdtemp()
        self.sdir = tempfile.mkdtemp()
        self.cwd = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", self.cwd], check=True)
        subprocess.run(["git", "-C", self.cwd, "config", "user.email", "s@t"],
                       check=True)
        subprocess.run(["git", "-C", self.cwd, "config", "user.name", "s"],
                       check=True)
        # challenge_brief off for the same reason engine_spec switches it off:
        # it spends one stubbed executor call before the loop and shifts every
        # attempt-count assertion; its behavior has its own spec.
        with open(os.path.join(self.cwd, ".delegation.json"), "w") as f:
            f.write('{"challenge_brief": false}\n')
        with open(os.path.join(self.cwd, "QWEN.md"), "w") as f:
            f.write("# rules\n")
        subprocess.run(["git", "-C", self.cwd, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.cwd, "commit", "-qm", "base"],
                       check=True)
        os.environ["DELEGATION_WORKTREES"] = tempfile.mkdtemp()
        os.environ["DELEGATION_LOCKS"] = tempfile.mkdtemp()
        os.environ["DELEGATION_REGISTRY"] = os.path.join(self.td, "reg.jsonl")
        cfg = os.path.join(self.td, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"autoedit_via_hook": False}, f)
        os.environ["DELEGATION_CONFIG"] = cfg
        from qd import invoke as _invoke
        self._compact_saved = _invoke.COMPACT_DIR
        _invoke.COMPACT_DIR = tempfile.mkdtemp()
        self.stub = os.path.join(self.td, "stub.py")
        with open(self.stub, "w") as f:
            f.write(STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IEXEC)
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.shutdown()
            srv.server_close()
        os.environ.clear()
        os.environ.update(self._env)
        from qd import invoke as _invoke
        _invoke.COMPACT_DIR = self._compact_saved

    def serve(self, **kw):
        srv = serve(**kw)
        self.servers.append(srv)
        return srv

    def profile_dict(self, url=None, env_key=None):
        p = {"argv": [sys.executable, self.stub, "-p", "{task}",
                      "--approval-mode", "{mode}", "-o", "json",
                      "-r", "{resume}"],
             "env": {"STUB_DIR": self.sdir}}
        if url:
            entry = {"id": "m", "baseUrl": url}
            if env_key:
                entry["envKey"] = env_key
            p["settings_overlay"] = {"modelProviders": {"openai": [entry]}}
        return p

    def machine(self, url=None, default=None, extra=None):
        profs = {"probe-stub": self.profile_dict(url)}
        if extra:
            profs.update(extra)
        data = {"profiles": profs}
        if default:
            data["default"] = default
        path = os.path.join(self.td, "executors.json")
        with open(path, "w") as f:
            json.dump(data, f)
        os.environ["DELEGATION_EXECUTORS"] = path

    def delegate(self, **over):
        from qd import engine
        args = {"task": "build out.py with MARKER", "cwd": self.cwd,
                "verify": "grep -q MARKER out.py",
                "approval_mode": "auto-edit", "executor": "probe-stub",
                "max_iterations": 2, "challenge_brief": False}
        args.update(over)
        return engine.delegate(args)

    def stub_ran(self):
        return os.path.exists(os.path.join(self.sdir, "attempt"))


class EngineProbe(Fixture):
    """Claim 2: refused before the first executor call, via the delegate path."""

    def test_dead_endpoint_refuses_before_any_spawn(self):
        srv = self.serve(code=502)
        self.machine(url=url_of(srv))
        r = self.delegate()
        self.assertEqual(r["status"], "refused")
        self.assertIn("EXECUTOR UNREACHABLE:", r["result_text"])
        self.assertIn("HTTP 502", r["result_text"])
        self.assertFalse(self.stub_ran())
        self.assertEqual(srv.hits, 1)

    def test_closed_port_refuses_before_any_spawn(self):
        self.machine(url=closed_port_url())
        r = self.delegate()
        self.assertEqual(r["status"], "refused")
        self.assertIn("EXECUTOR UNREACHABLE:", r["result_text"])
        self.assertFalse(self.stub_ran())

    def test_live_endpoint_proceeds_into_the_normal_path(self):
        srv = self.serve(code=200)
        self.machine(url=url_of(srv))
        r = self.delegate()
        self.assertEqual(r["status"], "success")
        self.assertTrue(self.stub_ran())
        self.assertEqual(srv.hits, 1)

    def test_401_endpoint_proceeds(self):
        # A worker with credentials may succeed where the bare probe got 401;
        # refusing here would refuse every auth-proxied endpoint.
        srv = self.serve(code=401)
        self.machine(url=url_of(srv))
        r = self.delegate()
        self.assertEqual(r["status"], "success")

    def test_probed_flag_skips_reprobing(self):
        # Claim 3, engine half: a link/item the server already probed for must
        # not probe again (the stub itself never talks to the endpoint, so a
        # dead server plus the flag still succeeds -- which is exactly what
        # proves the skip).
        from qd import engine
        srv = self.serve(code=502)
        self.machine(url=url_of(srv))
        r = self.delegate(**{engine.PROBED_ARG: True})
        self.assertEqual(r["status"], "success")
        self.assertEqual(srv.hits, 0)

    def test_no_base_url_means_no_probe_and_no_refusal(self):
        self.machine(url=None)
        r = self.delegate()
        self.assertEqual(r["status"], "success")

    def test_no_verdict_cache_across_calls(self):
        # A just-restarted endpoint must work on the NEXT call.
        srv = self.serve(code=502)
        self.machine(url=url_of(srv))
        r = self.delegate()
        self.assertEqual(r["status"], "refused")
        srv.code = 200
        r = self.delegate()
        self.assertEqual(r["status"], "success")
        self.assertEqual(srv.hits, 2)


class CallLevelProbe(Fixture):
    """Claim 3: chain/batch probe each distinct endpoint once, at the head."""

    def test_batch_probes_once_for_a_shared_endpoint(self):
        from qd import server
        srv = self.serve(code=200)
        self.machine(url=url_of(srv))
        text = server.run_delegate_batch({
            "cwd": self.cwd, "executor": "probe-stub",
            "challenge_brief": False,
            "batch": [
                {"task": "a", "verify": "grep -q MARKER out.py"},
                {"task": "b", "verify": "grep -q MARKER out.py"},
            ]})
        self.assertEqual(text.count("STATUS: success"), 2, text)
        self.assertEqual(srv.hits, 1)

    def test_batch_dead_endpoint_refuses_the_whole_call(self):
        from qd import server
        srv = self.serve(code=502)
        self.machine(url=url_of(srv))
        text = server.run_delegate_batch({
            "cwd": self.cwd, "executor": "probe-stub",
            "batch": [{"task": "a"}, {"task": "b"}]})
        self.assertTrue(text.startswith("STATUS: refused"), text)
        self.assertIn("EXECUTOR UNREACHABLE:", text)
        self.assertNotIn(server.BATCH_SEP, text)
        self.assertFalse(self.stub_ran())
        self.assertEqual(srv.hits, 1)

    def test_distinct_endpoints_each_probed_once_dead_one_named(self):
        from qd import server
        alive = self.serve(code=200)
        dead = self.serve(code=502)
        self.machine(url=url_of(alive),
                     extra={"probe-dead": self.profile_dict(url_of(dead))})
        text = server.run_delegate_batch({
            "cwd": self.cwd, "executor": "probe-stub",
            "batch": [{"task": "a"},
                      {"task": "b", "executor": "probe-dead"}]})
        self.assertTrue(text.startswith("STATUS: refused"), text)
        self.assertIn(url_of(dead).rstrip("/") + "/models", text)
        self.assertEqual(alive.hits, 1)
        self.assertEqual(dead.hits, 1)
        self.assertFalse(self.stub_ran())

    def test_chain_probes_at_the_head(self):
        from qd import server
        srv = self.serve(code=502)
        self.machine(url=url_of(srv))
        text = server.run_delegate_batch({
            "cwd": self.cwd, "executor": "probe-stub",
            "chain": [{"task": "a", "verify": "grep -q MARKER out.py"},
                      {"task": "b", "verify": "true"}]})
        self.assertIn("EXECUTOR UNREACHABLE:", text)
        self.assertFalse(self.stub_ran())
        self.assertEqual(srv.hits, 1)

    def test_stamps_items_and_nested_links(self):
        # The mechanism behind once-per-call: after one clean probe, every
        # item AND every link of a batch-of-chains carries PROBED_ARG, so no
        # engine entry re-probes.
        from qd import engine, server
        srv = self.serve(code=200)
        self.machine(url=url_of(srv))
        items = [{"task": "a", "chain": [{"task": "l1"}, {"task": "l2"}]},
                 {"task": "b"}]
        out = server._endpoint_refusal(
            {"cwd": self.cwd, "executor": "probe-stub", "batch": items}, items)
        self.assertIsNone(out)
        self.assertEqual(srv.hits, 1)
        self.assertTrue(items[0][engine.PROBED_ARG])
        self.assertTrue(items[1][engine.PROBED_ARG])
        for link in items[0]["chain"]:
            self.assertTrue(link[engine.PROBED_ARG])


class QueryProbe(Fixture):
    """Claim 4: the synchronous read path refuses the same way."""

    def test_query_refuses_on_dead_endpoint(self):
        from qd import queries
        srv = self.serve(code=502)
        self.machine(url=url_of(srv))
        out = queries.run_query({"question": "what is here?",
                                 "cwd": self.cwd, "executor": "probe-stub"})
        self.assertTrue(out.startswith("STATUS: refused"), out)
        self.assertIn("EXECUTOR UNREACHABLE:", out)
        self.assertFalse(self.stub_ran())

    def test_query_proceeds_on_live_endpoint(self):
        from qd import queries
        srv = self.serve(code=200)
        self.machine(url=url_of(srv))
        out = queries.run_query({"question": "what is here?",
                                 "cwd": self.cwd, "executor": "probe-stub"})
        self.assertTrue(out.startswith("STATUS: ok"), out)
        self.assertEqual(srv.hits, 1)


class DoctorProbe(Fixture):
    """Claim 5: the config sweep names a down endpoint."""

    def test_project_check_reports_endpoint_down(self):
        from qd import doctor
        srv = self.serve(code=502)
        self.machine(url=url_of(srv), default="probe-stub")
        finds = {f["id"]: f for f in doctor.project_check(self.cwd)}
        self.assertIn("endpoint-down", finds)
        self.assertEqual(finds["endpoint-down"]["severity"], "high")
        self.assertIn("HTTP 502", finds["endpoint-down"]["text"])

    def test_project_check_quiet_when_alive(self):
        from qd import doctor
        srv = self.serve(code=200)
        self.machine(url=url_of(srv), default="probe-stub")
        ids = [f["id"] for f in doctor.project_check(self.cwd)]
        self.assertNotIn("endpoint-down", ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
