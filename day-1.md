You are building day 1 of Odysseus, a minimal agent harness in Python. Standard
library only, no third-party packages, Python 3.10+.

Set up the repository first. Create a project folder, run git init, add a
.gitignore with __pycache__/, *.pyc, .DS_Store, and commit after each working
milestone. All harness code lives in a package folder named odysseus/ at the repo
root — create an empty odysseus/__init__.py now (day 4 fills it) — and demos
live in demos/. By day 4, python3 -m odysseus will run from the root. 
Project in git - https://github.com/shamim23/Xcalibur0

Code standard, which applies today and every following day. This build is the
reference implementation students keep for years, not scratch code. Every file
opens with a module docstring naming the day, the concept the file teaches, and
the design rules it embodies. Every public function carries a docstring. Where
behavior is subtle — the encrypted-reasoning replay, the call-id pairing, the
edit uniqueness rule, the torn-line tolerance — a short comment states the
constraint the code serves. Size targets, within roughly ten percent and
including documentation: provider.py 140 lines, loop.py 65, tools.py 155,
security.py 60, context.py 50, memory.py 50, skills.py 50, session.py 65,
subagent.py 27, harness.py 125, cli.py 80, fleet.py 36 — about 903 lines for the
finished package. Landing far under a target means documentation was skipped,
not that elegance was achieved.


Write odysseus/provider.py to this exact contract. It targets the OpenAI
Responses API and a reasoning model, so two things differ from a plain
chat-completions provider: there is no sampling temperature (a reasoning model
is steered by an effort dial, not temperature), and each turn's reasoning must
be captured and replayed on the next call for a stateless tool loop to work —
the direct analog of a Gemini thoughtSignature echo.

- Constants: API_ROOT = "https://api.openai.com/v1", DEFAULT_MODEL =
  "gpt-5.6-terra".
- api_key(): return xcalibur_key.sh from the environment; otherwise parse the
  first KEY=VALUE line of xcalibur_key.sh in the working directory (tolerating
  an `export` prefix and surrounding quotes). Raise RuntimeError with a clear
  message when neither is set.
- complete(model, system, messages, tools) returns {"text": str, "tool_calls":
  [{"name", "args", "signature"}], "usage": {"input": int, "output": int}}. It
  POSTs to API_ROOT + "/responses" a body with instructions = system, input =
  _to_wire(messages), max_output_tokens = 65536, reasoning = {"effort":
  "medium"}, store = False, and include = ["reasoning.encrypted_content"]. Do
  not send a temperature field. store False keeps the call stateless; the
  include line asks the API to hand back the encrypted reasoning that the tool
  loop must replay. When tools are given (a list of spec dicts, each {"schema":
  ...}), add tools = [{**t["schema"], "type": "function"} for t in tools] —
  Responses uses flat function tools, the schema dict with a "type": "function"
  key merged in, not the chat-completions {"function": ...} wrapper.
- The neutral message format: {"role": "user", "text"}; {"role": "assistant",
  "text", "tool_calls"} where each tool call is {"name", "args", "signature"};
  {"role": "tool", "name", "text"}. This is identical to every other Odysseus
  provider so loop.py stays vendor-agnostic; the OpenAI-specific idea of a
  call_id never appears here.
- _to_wire maps the neutral list to a Responses input list. A user message
  becomes {"role": "user", "content": text}. An assistant message becomes, in
  order: first, when the turn made tool calls, a reasoning item {"type":
  "reasoning", "summary": [], "encrypted_content": sig} where sig is the
  signature stored on the turn's first tool call (skip the item when there is no
  signature) — the reasoning must precede the calls it explains; then, when text
  is non-empty, {"role": "assistant", "content": text}; then one {"type":
  "function_call", "call_id": cid, "name": name, "arguments": json.dumps(args)}
  per call. A tool message becomes {"type": "function_call_output", "call_id":
  cid, "output": text}. OpenAI pairs a result to its call by call_id, not by
  name, so mint call_ids positionally: keep a running counter for the ids you
  emit on function_call items, push each onto a FIFO queue, and have each
  function_call_output consume the next id from that queue. Because the loop
  appends tool results in call order, this reconstructs the correct pairing with
  no help from loop.py or the neutral format.
- Response parsing: walk the response's "output" list by item "type".
  A "reasoning" item: capture its "encrypted_content" as the turn's signature.
  A "message" item: concatenate the "text" of each content part whose "type" is
  "output_text". A "function_call" item: collect {"name": name, "args":
  json.loads(arguments or "{}"), "signature": None}. After the walk, stamp the
  turn's captured signature onto every collected tool call, so the loop stores
  it per call (mirroring a per-call thoughtSignature) and _to_wire can replay it
  on the round-trip. Read usage.input_tokens and usage.output_tokens into usage
  as {"input", "output"}.
- _post(url, body, retries=5): urllib, headers Content-Type application/json and
  Authorization: "Bearer " + api_key(), timeout 600. On HTTP 429/500/502/503:
  sleep 2**attempt * 2 seconds and retry. On other HTTP errors: raise
  RuntimeError with the status and the first 400 characters of the error body.
  Retry URLError/TimeoutError the same way.

Write odysseus/loop.py to this exact contract.
- run_loop(model, system, messages, tools, on_event, before_tool, max_turns=80,
  before_turn=None) -> str. tools is a dict name -> Tool, where a Tool exposes
  .spec (a {"schema": ...} dict) and .run (a callable taking keyword
  arguments); the loop hands the provider [t.spec for t in tools.values()].
  on_event(kind, payload) fires with "assistant" after each model reply, and
  "tool_start" / "tool_end" around each execution. before_tool(call) returns
  None to allow or a reason string to block; a blocked call gets the tool result
  "BLOCKED: <reason>". before_turn, when provided, is called with the message
  list before each model call and its return value replaces the list in place —
  leave it unused today; day 3 plugs compaction in here.
- Each turn: provider.complete, append {"role": "assistant", "text",
  "tool_calls"}, emit the event. No tool calls: return the text. Otherwise run
  each call in order and append {"role": "tool", "name", "text": str(result)}.
- Unknown tool: result "ERROR: unknown tool <name>". Tool exception: result
  "ERROR: <ExceptionType>: <message>". The loop never crashes because a tool did.
- When max_turns runs out: append a user message "Turn limit reached; wrap up
  now.", make one final call with no tools, append and return its text.

Write demos/day1_dice.py: a hand-written roll_dice tool — a small object with
.spec = {"schema": {"name": "roll_dice", "description": "Roll count six-sided
dice", "parameters": {"type": "object", "properties": {"count": {"type":
"string", "description": "How many dice"}}, "required": ["count"]}}} and .run
returning the rolls — plus a printing on_event, an always-allow before_tool, and
the task "Roll 3 dice and tell me whether the total beats 10".

Verify before finishing: (1) the dice transcript shows user, assistant tool call,
tool result, assistant answer; (2) the prompt "Build a landing page for a coffee
shop" returns text and no tool calls; (3) git log shows the day's commits.