"""Acceptance items the user sees, driven through the browser."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from typing import Any

import pytest
from roboact.catalog import en
from roboact.flow_file import dump_json

from tests.acceptance_flows import (
    approval_flow,
    chain,
    fail_flow,
    loop_flow,
    loop_without_exit,
    stale_flow,
    version_flow,
)
from tests.conftest import ROOT, launch_chromium
from tests.http_api import http_json, http_status, put_flow
from tests.test_flow_file import run_doc
from tests.test_ui import _launch, _page


class _UI:
    def __init__(self, server: dict[str, Any]) -> None:
        self.server = server
        self.base = server["base"]
        self.p, _path = _launch()
        self.br = launch_chromium(self.p)

    def open(self) -> Any:
        page, _blocked, _errs = _page(self.br, self.base)
        page.add_init_script("localStorage.setItem('roboact.lang','en');")
        page.goto(self.base + "/", wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=120000)
        return page

    def close(self) -> None:
        self.br.close()
        self.p.stop()


@pytest.fixture
def ui(server):
    session = _UI(server)
    try:
        yield session
    finally:
        session.close()


def _wait(page: Any, *statuses: str, timeout: int = 60000) -> None:
    page.wait_for_function(
        """(wanted) => {
          const r = window.__af && window.__af.run && window.__af.run.value
          return !!(r && wanted.includes(r.status))
        }""",
        arg=list(statuses),
        timeout=timeout,
    )


def _rid(page: Any) -> str:
    rid = page.evaluate("() => { const r = window.__af.run.value; return r && (r.run_id || r.id) }")
    assert rid
    return str(rid)


def _run_body(base: str, rid: str) -> dict[str, Any]:
    body = http_json(base, f"/api/runs/{rid}")
    assert isinstance(body, dict)
    return body


def _records(base: str, rid: str) -> list[dict[str, Any]]:
    body = _run_body(base, rid)
    rows = body.get("records") or []
    assert isinstance(rows, list)
    return rows


def _ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r["node_id"]) for r in rows}


def _open_log(page: Any, nid: str, needle: str) -> str:
    page.locator(f".vue-flow__node[data-id='{nid}']").click()
    page.get_by_role("button", name="Log").click()
    page.locator("aside.right").get_by_text(needle).first.wait_for(timeout=15000)
    return str(page.locator("aside.right").inner_text())


def _restart(server: dict[str, Any]) -> None:
    proc = server["proc"]
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    env = {
        **os.environ,
        "ROBOACT_PORT": str(server["port"]),
        "ROBOACT_DB": str(server["db"]),
        "ROBOACT_TEST": "1",
        "PYTHONPATH": str(ROOT),
    }
    new = subprocess.Popen(
        [sys.executable, "-m", "roboact", "--port", str(server["port"]), "--db", str(server["db"])],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    server["proc"] = new
    t0 = time.time()
    while time.time() - t0 < 8:
        if new.poll() is not None:
            out = new.stdout.read().decode() if new.stdout else ""
            raise RuntimeError("server exited: " + out)
        try:
            urllib.request.urlopen(server["base"] + "/api/bootstrap", timeout=0.3)
            return
        except Exception:
            time.sleep(0.05)
    new.kill()
    raise RuntimeError("server did not restart")


def _result_of(base: str, rid: str) -> Any:
    body = _run_body(base, rid)
    run = body.get("run") or body
    return run.get("result")


def test_2_breakpoint_stops_at_node_3(server, ui: _UI) -> None:
    """Before-run breakpoint on node 3: stop there; nodes 3 and 4 have no new records."""
    put_flow(server["base"], chain("bp", ["n1", "n2", "n3", "n4"], {"v": 1}))
    page = ui.open()
    page.locator(".vue-flow__node[data-id='n3'] .bp.before").click()
    page.locator("button.slot-1").click()
    _wait(page, "paused")
    assert "Paused · breakpoint" in page.locator(".run-word").inner_text()
    assert "Paused · before" in page.locator(".vue-flow__node[data-id='n3']").text_content()
    pause = page.evaluate("() => window.__af.run.value.pause")
    assert pause["node_id"] == "n3"
    assert pause["reason"] == "breakpoint"
    rows = _records(server["base"], _rid(page))
    assert "n1" in _ids(rows)
    assert "n2" in _ids(rows)
    assert "n3" not in _ids(rows)
    assert "n4" not in _ids(rows)


def test_3_edit_and_continue_downstream_sees_value(server, ui: _UI) -> None:
    """At a breakpoint, change one input; the downstream record shows that value."""
    put_flow(server["base"], chain("edit", ["n1", "n2", "n3", "n4"], {"v": 1}))
    page = ui.open()
    page.locator(".vue-flow__node[data-id='n3'] .bp.before").click()
    page.locator("button.slot-1").click()
    _wait(page, "paused")
    page.locator(".vue-flow__node[data-id='n3']").click()
    page.get_by_role("button", name="Input").click()
    page.locator("aside.right textarea").fill('{"value":{"v":99}}')
    page.locator("button.slot-1").click()
    _wait(page, "completed")
    rows = _records(server["base"], _rid(page))
    n4 = next(r for r in rows if r["node_id"] == "n4")
    assert n4["inputs"]["value"]["v"] == 99
    assert "99" in _open_log(page, "n4", "99")


def test_4_refresh_keeps_pause_and_records(server, ui: _UI) -> None:
    """Refresh while stopped at a breakpoint: same node, input and output still shown."""
    put_flow(server["base"], chain("refresh", ["n1", "n2", "n3"], {"token": "keep-me"}))
    page = ui.open()
    page.locator(".vue-flow__node[data-id='n2'] .bp.after").click()
    page.locator("button.slot-1").click()
    _wait(page, "paused")
    before = _open_log(page, "n2", "keep-me")
    assert "keep-me" in before
    assert "Paused · breakpoint" in page.locator(".run-word").inner_text()
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=120000)
    _wait(page, "paused")
    pause = page.evaluate("() => window.__af.run.value.pause")
    assert pause["node_id"] == "n2"
    assert pause["reason"] == "breakpoint"
    assert "Paused · breakpoint" in page.locator(".run-word").inner_text()
    after = _open_log(page, "n2", "keep-me")
    assert "keep-me" in after
    assert "token" in after


def test_5_single_middle_node_one_record(server, ui: _UI) -> None:
    """Run a middle node from the last run: only that node gets a new record."""
    put_flow(server["base"], chain("single", ["n1", "n2", "n3"], {"v": 1}))
    page = ui.open()
    page.locator("button.slot-1").click()
    _wait(page, "completed")
    page.locator(".vue-flow__node[data-id='n2']").click()
    page.get_by_role("button", name="Input").click()
    page.get_by_role("button", name="Run this only").click()
    page.wait_for_function(
        """() => {
          const r = window.__af.run.value
          return r && r.kind === 'node' && r.status === 'completed'
        }""",
        timeout=30000,
    )
    rows = _records(server["base"], _rid(page))
    assert _ids(rows) == {"n2"}


def test_6_group_run_only_two_middle_nodes(server, ui: _UI) -> None:
    """Box-select two middle nodes and run the group: only they get records."""
    put_flow(server["base"], chain("group", ["a", "b", "c"], {"v": 1}))
    page = ui.open()
    page.locator("button.slot-1").click()
    _wait(page, "completed")
    first = _rid(page)
    upstream = next(r for r in _records(server["base"], first) if r["node_id"] == "start")
    box_a = page.locator(".vue-flow__node[data-id='a']").bounding_box()
    box_b = page.locator(".vue-flow__node[data-id='b']").bounding_box()
    assert box_a and box_b
    x0 = min(box_a["x"], box_b["x"]) - 24
    y0 = min(box_a["y"], box_b["y"]) - 24
    x1 = max(box_a["x"] + box_a["width"], box_b["x"] + box_b["width"]) + 24
    y1 = max(box_a["y"] + box_a["height"], box_b["y"] + box_b["height"]) + 24
    page.keyboard.down("Shift")
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x1, y1, steps=12)
    page.mouse.up()
    page.keyboard.up("Shift")
    selected = page.evaluate("() => (window.__af.vf.getSelectedNodes.value || []).map(n => n.id)")
    assert set(selected) == {"a", "b"}, selected
    page.keyboard.press("Control+g")
    page.locator(".vue-flow__pane").click(position={"x": 8, "y": 8})
    page.get_by_role("button", name="Run this group").click()
    page.wait_for_function(
        """() => {
          const r = window.__af.run.value
          return r && r.kind === 'group' && r.status === 'completed'
        }""",
        timeout=30000,
    )
    rows = _records(server["base"], _rid(page))
    assert _ids(rows) == {"a", "b"}
    entry = next(r for r in rows if r["node_id"] == "a")
    assert entry["inputs"]["value"] == upstream["outputs"]["data"]


def test_7_rerun_after_error_skips_upstream(server, ui: _UI) -> None:
    """After a node fails, rerun from it: upstream nodes get no new records."""
    put_flow(server["base"], fail_flow())
    page = ui.open()
    page.locator("button.slot-1").click()
    _wait(page, "error")
    parent = _rid(page)
    before = _records(server["base"], parent)
    _open_log(page, "bad", "· error")
    page.get_by_role("button", name="Input").click()
    page.get_by_role("button", name="Rerun from here").click()
    page.wait_for_function(
        """(parentId) => {
          const r = window.__af.run.value
          const id = r && (r.run_id || r.id)
          return r && id && id !== parentId && (r.status === 'error' || r.status === 'completed')
        }""",
        arg=parent,
        timeout=30000,
    )
    child = _rid(page)
    assert child != parent
    assert "start" not in _ids(_records(server["base"], child))
    assert "n1" not in _ids(_records(server["base"], child))
    again = _records(server["base"], parent)
    assert [r["step"] for r in again if r["node_id"] == "n1"] == [
        r["step"] for r in before if r["node_id"] == "n1"
    ]


def test_9_version_lock_shows_banner_rerun(server, ui: _UI) -> None:
    """Editing the canvas at a breakpoint keeps the old version and shows banner.rerun."""
    put_flow(server["base"], version_flow())
    page = ui.open()
    page.locator(".vue-flow__node[data-id='cond'] .bp.before").click()
    page.locator("button.slot-1").click()
    _wait(page, "paused")
    locked = page.evaluate("() => window.__af.run.value.flow_version")
    page.locator(".vue-flow__node[data-id='cond']").click()
    page.get_by_role("button", name="Params").click()
    field = page.locator("label", has_text="Compare to").locator(
        "xpath=following-sibling::input[1]"
    )
    field.fill("2")
    field.press("Tab")
    page.get_by_role("button", name="Rerun from the new version").wait_for(timeout=10000)
    banner = page.locator(".banners").inner_text()
    assert "Rerun from the new version" in banner
    assert f"v{locked}" in banner
    page.locator("button.slot-1").click()
    _wait(page, "completed")
    rows = _records(server["base"], _rid(page))
    assert any(r["node_id"] == "yesn" and r["status"] == "done" for r in rows)
    assert not any(r["node_id"] == "non" and r["status"] == "done" for r in rows)
    still = page.evaluate("() => window.__af.run.value.flow_version")
    assert still == locked


def test_11_restart_final_output_matches(server, ui: _UI) -> None:
    """Stop at a breakpoint, restart the server, continue: final output matches a clean run."""
    base = server["base"]
    flow = put_flow(base, chain("restart", ["n1", "n2"], {"token": "same"}))
    clean = http_json(
        base,
        "/api/runs",
        "POST",
        {"flow_id": flow["id"], "flow_version": flow["version"], "max_steps": 20},
    )
    t0 = time.time()
    clean_body: dict[str, Any] = {}
    while time.time() - t0 < 20:
        clean_body = _run_body(base, clean["run_id"])
        if (clean_body.get("run") or clean_body).get("status") == "completed":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("clean run did not finish")
    http_json(
        base,
        f"/api/flows/{flow['id']}/breakpoints/n2/before",
        "PUT",
        {"enabled": True, "mode": "every"},
    )
    started = http_json(
        base,
        "/api/runs",
        "POST",
        {"flow_id": flow["id"], "flow_version": flow["version"], "max_steps": 20},
    )
    t0 = time.time()
    while time.time() - t0 < 20:
        body = _run_body(base, started["run_id"])
        if (body.get("run") or body).get("status") == "paused":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("did not pause")
    _restart(server)
    page = ui.open()
    _wait(page, "paused")
    assert page.evaluate("() => window.__af.run.value.pause.node_id") == "n2"
    assert "Paused · after restart" in page.locator(".run-word").inner_text()
    page.locator("button.slot-1").click()
    _wait(page, "completed")
    assert _result_of(server["base"], _rid(page)) == (clean_body.get("run") or clean_body).get(
        "result"
    )


def test_12_staleness_slash_and_src_stale(server, ui: _UI) -> None:
    """Downstream-only edit does not stale upstream; an upstream edit shows slash and src.stale."""
    base = server["base"]
    put_flow(base, stale_flow())
    page = ui.open()
    page.locator("button.slot-1").click()
    _wait(page, "completed")
    full = _rid(page)
    page.locator(".vue-flow__node[data-id='down']").click()
    page.get_by_role("button", name="Params").click()
    compare = page.locator("label", has_text="Compare to").locator(
        "xpath=following-sibling::input[1]"
    )
    compare.fill("2")
    compare.press("Tab")
    page.wait_for_timeout(400)
    page.get_by_role("button", name="Input").click()
    page.get_by_role("button", name="Run this only").click()
    page.wait_for_function(
        """() => {
          const r = window.__af.run.value
          return r && r.kind === 'node' && r.status === 'completed'
        }""",
        timeout=30000,
    )
    upstream = [r for r in _records(base, full) if r["node_id"] == "up"]
    assert upstream
    assert upstream[-1]["stale"]["is_stale"] is False
    page.locator(".vue-flow__node[data-id='up']").click()
    page.get_by_role("button", name="Params").click()
    value = page.locator("label", has_text="Value").locator("xpath=following-sibling::input[1]")
    value.fill("2")
    value.press("Tab")
    page.evaluate(
        """() => {
          const n = window.__af.nodes.value.find(x => x.id === 'down')
          window.__af.vf.setCenter(n.position.x + 120, n.position.y + 40, { zoom: 1 })
        }"""
    )
    page.locator(".vue-flow__node[data-id='down'] .src").filter(
        has_text="Cache · upstream changed"
    ).wait_for(timeout=10000)
    slash = page.evaluate(
        """() => {
          const h = document.querySelector(
            ".vue-flow__node[data-id='down'] .vue-flow__handle.stale"
          )
          if (!h) return null
          const after = getComputedStyle(h, '::after')
          return { width: after.width, transform: after.transform, content: after.content }
        }"""
    )
    assert slash is not None
    assert slash["width"] == "9px"
    assert slash["transform"] not in ("none", "")


def test_13_approval_reject_and_second_page(server, ui: _UI) -> None:
    """Approval stops the run; Reject runs only that branch; the second page is told it lost."""
    put_flow(server["base"], approval_flow())
    page = ui.open()
    page.locator("button.slot-1").click()
    _wait(page, "paused")
    ask = page.locator(".vue-flow__node[data-id='ask']").text_content()
    assert "Awaiting confirmation" in ask
    rows = _records(server["base"], _rid(page))
    assert "ok" not in _ids(rows)
    assert "no" not in _ids(rows)
    other = ui.open()
    _wait(other, "paused")
    page.evaluate("() => { window.__af.decide('reject') }")
    other.evaluate("() => { window.__af.decide('reject') }")
    told = ""
    deadline = time.time() + 10
    while time.time() < deadline and not told:
        for viewer in (page, other):
            loc = viewer.locator(".mismatch")
            if loc.count() and "already" in loc.inner_text().lower():
                told = loc.inner_text()
                break
        time.sleep(0.05)
    assert "already" in told.lower()
    _wait(page, "completed")
    done = _records(server["base"], _rid(page))
    assert any(r["node_id"] == "no" and r["status"] == "done" for r in done)
    assert not any(r["node_id"] == "ok" and r["status"] == "done" for r in done)
    assert "Skipped" in page.locator(".vue-flow__node[data-id='ok']").text_content()


def test_14_loop_exit_three_records(server, ui: _UI) -> None:
    """A loop that exits on round 3 records rounds 1, 2 and 3 on each loop node."""
    put_flow(server["base"], loop_flow(3))
    page = ui.open()
    page.locator("button.slot-1").click()
    _wait(page, "completed")
    rows = _records(server["base"], _rid(page))
    for nid in ("body", "cond"):
        got = [r["round"] for r in rows if r["node_id"] == nid]
        assert got == [1, 2, 3], (nid, got)
        text = _open_log(page, nid, "round 1")
        assert "round 1" in text
        assert "round 2" in text
        assert "round 3" in text


def test_15_step_limit_1000(server, ui: _UI) -> None:
    """A loop that never exits stops at step 1000 with run.word.step_limit and no step 1001."""
    put_flow(server["base"], loop_flow(10**9, step_limit=1000))
    page = ui.open()
    page.locator(".vue-flow__node[data-id='body']").click()
    limit = page.locator("label", has_text="Step limit").locator(
        "xpath=following-sibling::input[1]"
    )
    limit.fill("1000")
    assert limit.input_value() == "1000"
    page.locator("button.slot-1").click()
    _wait(page, "step_limit", timeout=180000)
    assert "Step limit reached" in page.locator(".run-word").inner_text()
    page.evaluate("() => { window.__af.selected.value = null }")
    assert "Stopped at the step limit" in page.locator("aside.right").inner_text()
    body = _run_body(server["base"], _rid(page))
    run = body.get("run") or body
    assert run["status"] == "step_limit"
    assert run["steps_used"] == 1000
    assert run["max_steps"] == 1000
    steps = [r["step"] for r in body["records"]]
    assert max(steps) == 1000
    assert 1001 not in steps
    assert len(steps) == 1000


def test_16_loop_breakpoint_stops_every_round(server, ui: _UI) -> None:
    """A loop breakpoint with default settings stops every round."""
    put_flow(server["base"], loop_flow(3))
    page = ui.open()
    page.locator(".vue-flow__node[data-id='body'] .bp.before").click()
    page.locator("button.slot-1").click()
    _wait(page, "paused")
    assert page.evaluate("() => window.__af.run.value.pause.round") == 1
    assert [r for r in _records(server["base"], _rid(page)) if r["node_id"] == "body"] == []
    page.locator("button.slot-1").click()
    page.wait_for_function(
        """() => {
          const r = window.__af.run.value
          return r && r.status === 'paused' && r.pause && r.pause.round === 2
        }""",
        timeout=30000,
    )
    rounds = [r["round"] for r in _records(server["base"], _rid(page)) if r["node_id"] == "body"]
    assert rounds == [1]


def test_16_1_breakpoint_round_2_has_only_round_1(server, ui: _UI) -> None:
    """A round-2 breakpoint does not stop on round 1; when it stops, only round 1 is recorded."""
    put_flow(server["base"], loop_flow(3))
    page = ui.open()
    page.locator(".vue-flow__node[data-id='body']").click()
    page.once("dialog", lambda dialog: dialog.accept("2"))
    page.get_by_role("button", name="Breakpoint: only stop on round N").click()
    page.locator("button.slot-1").click()
    _wait(page, "paused", timeout=30000)
    pause = page.evaluate("() => window.__af.run.value.pause")
    assert pause["node_id"] == "body"
    assert pause["round"] == 2
    rounds = [r["round"] for r in _records(server["base"], _rid(page)) if r["node_id"] == "body"]
    assert rounds == [1]


def test_17_single_run_uses_round_2_input(server, ui: _UI) -> None:
    """Run one loop node alone with round 2 input: that input, and no other node."""
    put_flow(server["base"], loop_flow(3))
    page = ui.open()
    page.locator("button.slot-1").click()
    _wait(page, "completed")
    parent = _rid(page)
    wanted = next(
        r for r in _records(server["base"], parent) if r["node_id"] == "body" and r["round"] == 2
    )
    page.locator(".vue-flow__node[data-id='body']").click()
    page.get_by_role("button", name="Input").click()
    page.locator("select", has=page.locator("option", has_text="Latest round")).select_option(
        label="Round 2"
    )
    page.get_by_role("button", name="Run this only").click()
    page.wait_for_function(
        """(parentId) => {
          const r = window.__af.run.value
          const id = r && (r.run_id || r.id)
          return r && r.kind === 'node' && id && id !== parentId && r.status === 'completed'
        }""",
        arg=parent,
        timeout=30000,
    )
    rows = _records(server["base"], _rid(page))
    assert _ids(rows) == {"body"}
    assert rows[0]["inputs"] == wanted["inputs"]


def test_18_run_refuses_loop_without_exit(server, ui: _UI) -> None:
    """Run on a loop with no condition exit is refused, and the message says what is missing."""
    put_flow(server["base"], loop_without_exit())
    page = ui.open()
    page.locator("button.slot-1").click()
    page.wait_for_function(
        """() => {
          const el = document.querySelector('.banner')
          const text = (el && el.textContent) || ''
          return text.includes('condition') && text.includes('exit')
        }""",
        timeout=10000,
    )
    runs = http_json(
        server["base"], "/api/runs?flow_id=" + page.evaluate("() => window.__af.meta.value.id")
    )
    assert runs["runs"] == [] or all(r["status"] not in ("running", "paused") for r in runs["runs"])


def test_18_1_restart_mid_loop_matches(server, ui: _UI) -> None:
    """Restart halfway through a loop; the final result matches a run with no restart."""
    base = server["base"]
    flow = put_flow(base, loop_flow(3))
    clean = http_json(
        base,
        "/api/runs",
        "POST",
        {"flow_id": flow["id"], "flow_version": flow["version"], "max_steps": 40},
    )
    t0 = time.time()
    clean_body: dict[str, Any] = {}
    while time.time() - t0 < 20:
        clean_body = _run_body(base, clean["run_id"])
        if (clean_body.get("run") or clean_body).get("status") == "completed":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("clean loop did not finish")
    http_json(
        base,
        f"/api/flows/{flow['id']}/breakpoints/body/before",
        "PUT",
        {"enabled": True, "mode": "round", "round": 2},
    )
    started = http_json(
        base,
        "/api/runs",
        "POST",
        {"flow_id": flow["id"], "flow_version": flow["version"], "max_steps": 40},
    )
    t0 = time.time()
    while time.time() - t0 < 20:
        body = _run_body(base, started["run_id"])
        run = body.get("run") or body
        if run.get("status") == "paused" and (run.get("pause") or {}).get("round") == 2:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("did not pause mid-loop")
    _restart(server)
    page = ui.open()
    _wait(page, "paused")
    assert page.evaluate("() => window.__af.run.value.pause.node_id") == "body"
    page.locator("button.slot-1").click()
    _wait(page, "completed")
    assert _result_of(server["base"], _rid(page)) == (clean_body.get("run") or clean_body).get(
        "result"
    )


def _note_yaml(quoted: bool) -> str:
    ver = '"1.10"' if quoted else "1.10"
    mode = '"on"' if quoted else "on"
    return (
        """
format: roboact-flow
format_version: 1
flow:
  id: "hand"
  name: "hand"
  revision: 1
  step_limit: 20
  action_list_version: "fake-v1"
  adapter: {name: "__ADAPTER__", version: "0.2", target: "sim"}
  robot: {id: "fake-1", model: "FakeArm"}
  units: {angle: "rad", length: "m"}
nodes:
  - id: "start"
    kind: start
    title: Start
    params: {data: {x: 0.1}}
    position: {x: 0, y: 0}
  - id: "note"
    kind: action
    action: fake_note
    title: Note
    params: {mode: __MODE__, ver: __VER__, token: "filled"}
    position: {x: 40, y: 120}
  - id: "out"
    kind: end
    title: Out
    params: {}
    position: {x: 320, y: 0}
edges:
  - from: {node: start, port: data}
    to: {node: out, port: value}
""".replace("__ADAPTER__", en("adapter.fake"))
        .replace("__MODE__", mode)
        .replace("__VER__", ver)
    )


def _import_text(page: Any, tmp_path: Any, name: str, text: str) -> None:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    page.locator("[data-testid=flow-export]").click()
    page.locator("[data-testid=flow-import]").set_input_files(str(path))


def test_20_5_blocked_run_names_node_and_quoted_runs(server, ui: _UI, tmp_path) -> None:
    """Unquoted 1.10 blocks Run and names the node and param; quoted 1.10 runs as that string."""
    page = ui.open()
    _import_text(page, tmp_path, "hand.yml", _note_yaml(False))
    page.wait_for_selector(".vue-flow__node[data-id='note']")
    assert page.locator("button.slot-1").is_disabled()
    assert "Note" in page.locator(".vue-flow__node[data-id='note']").inner_text()
    page.locator(".vue-flow__node[data-id='note']").click()
    assert page.locator(".param-bad label", has_text="Ver").count() == 1
    page.evaluate("() => window.__af.doRun('all')")
    page.wait_for_function(
        """() => {
          const el = document.querySelector('.banner')
          const text = (el && el.textContent) || ''
          return text.includes('Note') && text.includes('ver')
        }""",
        timeout=10000,
    )
    _import_text(page, tmp_path, "hand-quoted.yml", _note_yaml(True))
    page.wait_for_function(
        """() => {
          const b = document.querySelector('button.slot-1')
          return b && !b.disabled
        }""",
        timeout=10000,
    )
    page.locator(".vue-flow__node[data-id='note']").click()
    shown = page.locator("label", has_text="Ver").locator("xpath=following-sibling::input[1]")
    assert shown.input_value() == "1.10"
    page.locator("button.slot-1").click()
    _wait(page, "completed")
    fid = page.evaluate("() => window.__af.meta.value.id")
    body = _run_body(server["base"], _rid(page))
    run = body.get("run") or body
    assert run["status"] == "completed"
    flow = http_json(server["base"], f"/api/flows/{fid}")["flow"]
    note = next(n for n in flow["nodes"] if n["id"] == "note")
    assert note["params"]["ver"] == "1.10"
    assert isinstance(note["params"]["ver"], str)
    assert flow["version"] == run["flow_version"]


def test_20_8_replacing_action_unlocks_run(server, ui: _UI, tmp_path) -> None:
    """Unknown action locks Run; Review does not unlock it; replacing the action does."""
    doc = run_doc()
    doc["nodes"].append(
        {
            "action": "gone.action",
            "id": "ghost",
            "kind": "action",
            "params": {},
            "position": {"x": 80, "y": 80},
            "title": "Ghost",
        }
    )
    page = ui.open()
    _import_text(page, tmp_path, "ghost.json", dump_json(doc))
    page.locator("[data-testid=count-action-gone]").wait_for()
    assert page.locator("[data-testid=count-action-gone]").inner_text() == "1 missing action"
    assert page.locator("button.slot-1").is_disabled()
    assert "no longer provided" in page.locator(".vue-flow__node[data-id='ghost']").inner_text()
    page.evaluate("() => window.__af.reviewEnable()")
    assert page.locator("button.slot-1").is_disabled()
    code, _body = http_status(
        server["base"],
        "/api/runs",
        "POST",
        {
            "flow_id": page.evaluate("() => window.__af.meta.value.id"),
            "flow_version": page.evaluate("() => window.__af.meta.value.version"),
        },
    )
    assert code == 409
    page.evaluate(
        """() => {
          const n = window.__af.nodes.value.find(x => x.id === 'ghost')
          n.data = Object.assign({}, n.data, {
            nodeType: 'constant',
            params: { value: '1', value_type: 'string' },
          })
          window.__af.selected.value = 'ghost'
          window.__af.updateParam('value', '1')
        }"""
    )
    page.wait_for_function(
        """() => {
          const b = document.querySelector('button.slot-1')
          const gone = document.querySelector('[data-testid=count-action-gone]')
          return b && !b.disabled && !gone
        }""",
        timeout=10000,
    )
