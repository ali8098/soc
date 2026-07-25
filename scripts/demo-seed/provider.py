"""Scripted OpenAI-compatible provider (issue #72 demo seeding).

Deterministic playback of pre-authored triage responses — ZERO runtime
inference, no API tokens, no GPU. Serves the exact contract the product's
inference layer speaks for engine=openai_compatible (verified against
langchain_openai's function_calling path):

- POST /v1/chat/completions with tools + forced tool_choice
  (SupervisorDecision | ConstrainedSupervisorDecision | VerdictDraft)
- response: assistant message with tool_calls[0].function.{name,arguments}
  echoing the forced tool name, finish_reason="tool_calls", plus usage
  fields (prompt_tokens/completion_tokens) so budget tracking sees spend.

Routing: the seeder writes a manifest (JSON: script-key → play) before
injecting; script keys are unique host tokens that appear verbatim in the
supervisor/verdict prompts. Router calls advance a per-key route counter
(INVESTIGATE → VERDICT); verdict calls return the authored VerdictDraft.
Unmatched prompts (organic alerts triaged while the stub is active) get an
honest generic escalation so nothing silently closes unreviewed.

Run: uvicorn provider:app --port 8091   (manifest path via SEED_MANIFEST)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

app = FastAPI()

MANIFEST_PATH = Path(os.environ.get("SEED_MANIFEST", Path(__file__).parent / "manifest.json"))
_manifest: dict[str, Any] = {}
_manifest_mtime = 0.0
_route_counts: dict[str, int] = defaultdict(int)

GENERIC_VERDICT = {
    "decision": "escalate",
    "confidence": 0.55,
    "threat_assessment": "Alert outside the curated demo corpus; escalating for analyst review rather than guessing.",
    "evidence_strength": "weak",
    "potential_impact": "medium",
    "urgency": "elevated",
    "key_evidence": ["no curated assessment exists for this alert shape"],
    "gaps_in_evidence": ["full context not evaluated by the demo playback provider"],
    "assumptions_made": [],
    "alternative_explanations": [],
    "recommendation": "Escalate for human review (demo playback provider default).",
}


def _load_manifest() -> dict[str, Any]:
    global _manifest, _manifest_mtime
    try:
        mtime = MANIFEST_PATH.stat().st_mtime
        if mtime != _manifest_mtime:
            _manifest = json.loads(MANIFEST_PATH.read_text())
            _manifest_mtime = mtime
    except FileNotFoundError:
        _manifest = {}
    return _manifest


def _prompt_text(body: dict) -> str:
    parts = []
    for m in body.get("messages", []):
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(p.get("text", "") for p in c if isinstance(p, dict))
    return "\n".join(parts)


def _match_key(text: str) -> str | None:
    for key in _load_manifest():
        if key in text:
            return key
    return None


def _forced_tool(body: dict) -> str | None:
    tc = body.get("tool_choice")
    if isinstance(tc, dict):
        return (tc.get("function") or {}).get("name")
    return None


def _tool_response(model: str, tool_name: str, args: dict, prompt_len: int) -> dict:
    completion = json.dumps(args)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{uuid.uuid4().hex[:16]}",
                            "type": "function",
                            "function": {"name": tool_name, "arguments": completion},
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": max(1, prompt_len // 4),
            "completion_tokens": max(1, len(completion) // 4),
            "total_tokens": max(2, prompt_len // 4 + len(completion) // 4),
        },
    }


def _supervisor_args(key: str | None, text: str) -> dict:
    play = _load_manifest().get(key or "", {})
    route = play.get("route") or ["INVESTIGATE", "VERDICT"]
    counter_key = key or f"generic:{hash(text[:200]) & 0xFFFF}"
    step = _route_counts[counter_key]
    _route_counts[counter_key] += 1
    action = route[step] if step < len(route) else "VERDICT"
    reasoning = {
        "INVESTIGATE": "Pull the surrounding log context before drawing any conclusion.",
        "ENRICH": "Check reputation on the involved observables.",
        "CONTEXTUALIZE": "Consult threat intelligence before weighing the evidence.",
        "VERDICT": "Evidence is sufficient for a decision.",
        "CLOSE": "Deterministic disposition applies.",
    }.get(action, "Proceeding with triage.")
    return {
        "next_action": action,
        "action_reasoning": reasoning,
        "tp_confidence": 0.5 if action != "VERDICT" else 0.4,
        "confidence_reasoning": "Confidence tracks the evidence gathered so far.",
        "specific_instructions": None,
    }


@app.get("/v1/models")
async def models() -> dict:
    return {"object": "list", "data": [{"id": "demo-playback", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request) -> dict:
    body = await request.json()
    text = _prompt_text(body)
    model = body.get("model", "demo-playback")
    tool = _forced_tool(body)
    key = _match_key(text)

    if tool and "SupervisorDecision" in tool:
        return _tool_response(model, tool, _supervisor_args(key, text), len(text))
    if tool == "VerdictDraft":
        play = _load_manifest().get(key or "", {})
        verdict = play.get("verdict") or GENERIC_VERDICT
        return _tool_response(model, tool, verdict, len(text))
    if tool:
        # Unknown forced schema: echo an empty object and let validation
        # retry — safer than inventing fields for a schema we don't know.
        return _tool_response(model, tool, {}, len(text))

    # Plain completion (no schema): short deterministic content.
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Acknowledged."},
            }
        ],
        "usage": {
            "prompt_tokens": max(1, len(text) // 4),
            "completion_tokens": 2,
            "total_tokens": max(3, len(text) // 4 + 2),
        },
    }
