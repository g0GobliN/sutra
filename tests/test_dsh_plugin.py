"""The DeepSeek Harness bundle, driven the way the harness drives it.

The Python seam is covered by `tests/test_calls.py`; what these tests prove is
the wiring in `dsh_host_index.js`: that the `tools/post-execute` stage replaces
the text the model would read and quotes a recovery id the store can answer,
that `tools/pre-execute` denies a repeat of the same read and lets every other
tool through to the host's own decision, and that an interpreter the plugin
cannot start leaves both stages untouched. The bundle is loaded from a package
directory shaped like the one `dsh.py` installs, so the module type and the
import form are the host's, not the repository's.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import expose_package, plugin_module

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed")


CLUE = "CLUE: dsh-plugin-token-4A"


def _payload() -> str:
    """A JSON-lines result big enough for the engine to compress.

    The engine leaves a result alone when trimming cannot pay for the marker,
    so the size matters: at this length it rewrites the text and stores the
    original under a recovery id.
    """
    rows = [
        {"id": index, "name": f"item-{index}", "status": "ok",
         "note": "the same padding every row carries"}
        for index in range(1200)
    ]
    rows[600]["note"] = CLUE
    return "\n".join(json.dumps(row) for row in rows)


def _bundle(tmp_path):
    """A package directory shaped like the installed bundle."""
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"type": "module"}),
                                      encoding="utf-8")
    shutil.copy(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "dsh_host_index.js"),
        root / "index.js")
    return root


def _run(tmp_path, home, body, *, python=None):
    """Load the bundle under a stub host and run one scenario in it."""
    node = shutil.which("node")
    bundle = _bundle(tmp_path)
    library = expose_package(tmp_path / "lib" / "sutra").parent
    script = tmp_path / "scenario.mjs"
    script.write_text(
        f'import {{ apply }} from {json.dumps(str(bundle / "index.js"))}\n'
        "\n"
        "const handlers = {}\n"
        "const context = {\n"
        "  on: (event, handler) => { handlers[event] = handler },\n"
        "  get: () => ({ register: () => {} }),\n"
        "  logger: { info: () => {}, warn: () => {} },\n"
        "}\n"
        "apply(context, {\n"
        f"  python: {json.dumps(python or sys.executable)},\n"
        f"  pythonPath: {json.dumps(str(library))},\n"
        f"  home: {json.dumps(str(home))},\n"
        '  mode: "balanced",\n'
        "  minTextBytes: 8,\n"
        "})\n"
        "\n"
        + body,
        encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(library))
    return subprocess.run([node, str(script)], capture_output=True, text=True,
                          timeout=180, cwd=str(tmp_path), env=env)


def test_the_result_stage_replaces_the_text_and_the_store_keeps_the_original(
        tmp_path, monkeypatch):
    home = tmp_path / "home"
    source = tmp_path / "rows.jsonl"
    payload = _payload()
    source.write_text(payload, encoding="utf-8")
    body = (
        "const exec = { name: 'read_file', arguments: { path: "
        f"{json.dumps(str(source))}" + " }, agent: { session: { id: 's1' } } }\n"
        f"const text = {json.dumps(payload)}\n"
        "const decision = { kind: 'accept' }\n"
        "const kept = { ...decision, content: [{ type: 'text', text }] }\n"
        "const answer = await handlers['tools/post-execute'](\n"
        "  exec, { content: [{ type: 'text', text }] }, async () => kept)\n"
        "const rewritten = answer.content[0].text\n"
        "const match = rewritten.match(/id=(su_[0-9a-f]{32})/)\n"
        "console.log(JSON.stringify({\n"
        "  kind: answer.kind,\n"
        "  changed: rewritten !== text,\n"
        "  marker: rewritten.includes('[sutra:'),\n"
        "  id: match ? match[1] : '',\n"
        "}))\n"
    )
    finished = _run(tmp_path, home, body)
    assert finished.returncode == 0, finished.stdout + finished.stderr
    report = json.loads(finished.stdout.strip().splitlines()[-1])

    assert report["kind"] == "accept", "the stage keeps the host's decision"
    assert report["changed"] is True, "the text the model reads must be smaller"
    assert report["marker"] is True
    assert report["id"], "the marker must quote a recovery id"

    # The store is the authority on recovery: the CLI pages its answer for a
    # model's window, the stored bytes are whole.
    monkeypatch.setenv("SUTRA_STORE_DIR", str(home / "store"))
    store = plugin_module("store")
    keeper = store.store_from_env()
    found = keeper.get(report["id"])
    assert found.status == "ok", f"the id the marker quotes must resolve: {found.status}"
    assert found.entry is not None
    recovered = keeper.read_bytes(found.entry).decode("utf-8")
    assert recovered == payload, "recovery must be byte-identical to the original"
    assert CLUE in recovered, "no detail may be lost in the rewrite"

    asked = subprocess.run(
        [sys.executable, "-m", "sutra.cli", "restore", "--id", report["id"]],
        capture_output=True, text=True, timeout=60,
        env=dict(os.environ, PYTHONPATH=str(tmp_path / "lib"),
                 SUTRA_STORE_DIR=str(home / "store")))
    assert asked.returncode == 0, asked.stderr
    assert json.loads(asked.stdout)["status"] == "ok", "the agent's own path works"


def test_the_seam_denies_a_repeated_read_and_lets_a_write_through(tmp_path):
    home = tmp_path / "home"
    source = tmp_path / "rows.jsonl"
    source.write_text(_payload(), encoding="utf-8")
    body = (
        "const exec = { name: 'read_file', arguments: { path: "
        f"{json.dumps(str(source))}" + " }, agent: { session: { id: 's1' } } }\n"
        "const decision = { kind: 'allow' }\n"
        "const downstream = async () => decision\n"
        "const result = { content: [{ type: 'text', text: "
        f"{json.dumps(_payload())}" + " }] }\n"
        "await handlers['tools/post-execute'](exec, result, async () => (\n"
        "  { kind: 'accept', content: result.content }))\n"
        "const repeat = await handlers['tools/pre-execute'](exec, downstream)\n"
        "const write = await handlers['tools/pre-execute'](\n"
        "  { ...exec, name: 'write_file' }, downstream)\n"
        "console.log(JSON.stringify({\n"
        "  repeat: repeat.kind,\n"
        "  reason: typeof repeat.reason === 'string' ? repeat.reason : '',\n"
        "  write: write.kind,\n"
        "}))\n"
    )
    finished = _run(tmp_path, home, body)
    assert finished.returncode == 0, finished.stdout + finished.stderr
    report = json.loads(finished.stdout.strip().splitlines()[-1])

    assert report["repeat"] == "deny", "a stored read must not run twice"
    assert "sutra_restore" in report["reason"]
    assert report["write"] == "allow", "a write must reach the host's decision"


def test_an_interpreter_that_cannot_start_leaves_both_stages_alone(tmp_path):
    home = tmp_path / "home"
    body = (
        "const exec = { name: 'read_file', arguments: { path: 'rows.jsonl' },"
        " agent: { session: { id: 's1' } } }\n"
        "const kept = { kind: 'accept', content: [{ type: 'text', text: 'x' }] }\n"
        "const after = await handlers['tools/post-execute'](\n"
        "  exec, { content: [{ type: 'text', text: 'x' }] }, async () => kept)\n"
        "const before = await handlers['tools/pre-execute'](\n"
        "  exec, async () => ({ kind: 'allow' }))\n"
        "console.log(JSON.stringify({ after: after.content[0].text,\n"
        "  before: before.kind }))\n"
    )
    finished = _run(tmp_path, home, body, python=str(tmp_path / "no-python"))

    assert finished.returncode == 0, finished.stdout + finished.stderr
    report = json.loads(finished.stdout.strip().splitlines()[-1])
    assert report["after"] == "x", "an unreachable seam must not edit the result"
    assert report["before"] == "allow", "an unreachable seam must not block a call"
