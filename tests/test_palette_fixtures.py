"""append_note / tool_call stay off the product palette; control nodes have no loop."""
from __future__ import annotations

from roboact.adapter import FakeAdapter
from roboact.nodes import REGISTRY, bind_adapter, list_specs


def test_product_palette_hides_write_fixtures():
    bind_adapter(FakeAdapter())
    specs = list_specs()
    shown = [s for s in specs if s.get("palette")]
    types = {s["type_id"] for s in shown}
    assert "tool_call" not in types
    assert "append_note" not in types
    assert "loop" not in types
    assert "loop" not in REGISTRY
    for required in ("start", "output", "condition", "merge", "human_confirm", "constant"):
        assert required in types
    assert all(s["side_effect"] in ("none", "read", "motion", "write") for s in specs)
    assert all(s["side_effect"] != "write" for s in shown)
