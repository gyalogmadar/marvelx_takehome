"""Prompts are versioned: every render must match tests/golden/prompts.json byte for byte.
A deliberate prompt change regenerates the golden file (``python -m tests.prompt_renders``) and
shows up in review as a diff of exactly what the models will now be told."""
import json

from tests.prompt_renders import GOLDEN, render_all


def test_every_prompt_renders_exactly_as_recorded():
    golden = json.loads(GOLDEN.read_text())
    renders = json.loads(json.dumps(render_all(), ensure_ascii=False))
    assert renders.keys() == golden.keys()
    for key in golden:
        assert renders[key] == golden[key], f"{key} changed; if intended, run: python -m tests.prompt_renders"
