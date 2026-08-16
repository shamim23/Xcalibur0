"""Day 1 — the provider seam: one vendor's API hidden behind a neutral contract.

Concept taught: every Odysseus provider exposes the same ``complete(...)`` over
the same neutral message format, so ``loop.py`` never learns a vendor's wire
shape. Swapping providers is swapping this file.

This one targets the OpenAI Responses API with a reasoning model, which imposes
two rules a plain chat-completions provider does not have. First, no sampling
temperature: a reasoning model is steered by an effort dial, not by temperature,
so the field is never sent and no knob exists for it. Second, reasoning is
captured and replayed: with ``store: False`` the server keeps nothing between
calls, so the encrypted reasoning it hands back must be echoed on the next
request or the model loses the thinking behind its own mid-loop tool calls --
the direct analog of echoing a Gemini ``thoughtSignature``.

Design rule enforced here: OpenAI-only concepts stop at this file's edge. The
``call_id`` pairing a result to its call is minted and consumed inside
``_to_wire`` and never appears in the neutral format the loop passes around.
"""

import json
import os
import time
import urllib.error
import urllib.request
from collections import deque

API_ROOT = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.6-terra"

_KEY_FILE = "xcalibur_key.sh"
_KEY_ENV = "OPEN_AI_KEY"


def api_key():
    """Return the API key from the environment, else from ./xcalibur_key.sh.

    The file is a shell fragment, so tolerate an ``export`` prefix and quotes
    around the value; only the first KEY=VALUE line is read.
    """
    key = os.environ.get(_KEY_ENV)
    if key:
        return key.strip()
    path = os.path.join(os.getcwd(), _KEY_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                return line.partition("=")[2].strip().strip("'\"")
    raise RuntimeError("No API key: set %s in the environment or put a "
                       "KEY=VALUE line in ./%s." % (_KEY_ENV, _KEY_FILE))


def complete(model, system, messages, tools=None):
    """Run one model turn and return {"text", "tool_calls", "usage"}.

    ``messages`` is the neutral format; ``tools`` is a list of spec dicts, each
    ``{"schema": ...}``. Returned tool calls are {"name", "args", "signature"}.
    """
    # store False keeps the call stateless; the include line asks for the
    # encrypted reasoning the tool loop must replay next turn. No temperature.
    body = {"model": model or DEFAULT_MODEL, "instructions": system,
            "input": _to_wire(messages), "max_output_tokens": 65536,
            "reasoning": {"effort": "medium"}, "store": False,
            "include": ["reasoning.encrypted_content"]}
    if tools:
        # Responses uses flat function tools -- the schema with a type key
        # merged in, not the chat-completions {"function": ...} wrapper.
        body["tools"] = [{**t["schema"], "type": "function"} for t in tools]

    data = _post(API_ROOT + "/responses", body)

    text_parts, calls, signature = [], [], None
    for item in data.get("output") or []:
        kind = item.get("type")
        if kind == "reasoning":
            signature = item.get("encrypted_content") or signature
        elif kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    text_parts.append(part.get("text") or "")
        elif kind == "function_call":
            calls.append({"name": item.get("name"), "signature": None,
                          "args": json.loads(item.get("arguments") or "{}")})
    # One reasoning blob covers the whole turn, but it is stamped onto every
    # call so the loop can store it per call (mirroring a per-call
    # thoughtSignature) and _to_wire can replay it on the round trip.
    for call in calls:
        call["signature"] = signature

    usage = data.get("usage") or {}
    return {"text": "".join(text_parts), "tool_calls": calls,
            "usage": {"input": usage.get("input_tokens") or 0,
                      "output": usage.get("output_tokens") or 0}}


def _to_wire(messages):
    """Map the neutral message list onto a Responses ``input`` list."""
    wire, pending, counter = [], deque(), 0
    for message in messages:
        role = message.get("role")
        if role == "user":
            wire.append({"role": "user", "content": message.get("text") or ""})
        elif role == "assistant":
            calls = message.get("tool_calls") or []
            # The reasoning item must precede the calls it explains.
            if calls:
                signature = calls[0].get("signature")
                if signature:
                    wire.append({"type": "reasoning", "summary": [],
                                 "encrypted_content": signature})
            text = message.get("text") or ""
            if text:
                wire.append({"role": "assistant", "content": text})
            for call in calls:
                counter += 1
                call_id = "call_%d" % counter
                pending.append(call_id)
                wire.append({"type": "function_call", "call_id": call_id,
                             "name": call.get("name"),
                             "arguments": json.dumps(call.get("args") or {})})
        elif role == "tool":
            # OpenAI pairs a result to its call by call_id, never by name. The
            # loop appends results in call order, so consuming the FIFO of ids
            # minted above reconstructs the pairing with no help from loop.py.
            wire.append({"type": "function_call_output",
                         "call_id": pending.popleft() if pending else "call_0",
                         "output": message.get("text") or ""})
    return wire


def _post(url, body, retries=5):
    """POST JSON and return the decoded reply, retrying transient failures."""
    payload = json.dumps(body).encode("utf-8")
    for attempt in range(retries):
        request = urllib.request.Request(url, data=payload, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", "Bearer " + api_key())
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            detail = error.read().decode("utf-8", "replace")[:400]
            raise RuntimeError("HTTP %s from %s: %s" % (error.code, url, detail))
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise RuntimeError("Network error calling %s: %s" % (url, error))
    raise RuntimeError("Exhausted %d attempts calling %s" % (retries, url))
