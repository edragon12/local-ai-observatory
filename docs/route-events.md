# observatory/v1

A router you run yourself can describe a turn without this package including a router. Three deliveries are accepted. The chat request is not one of them: a router sits after the chat app, so it cannot write the request the filter already saw.

1. **Response stream.** Put an `observatory` object on an OpenAI-compatible response chunk. The Open WebUI filter copies the allowlisted fields and posts them to the receiver. Whether a given Open WebUI version actually passes a custom chunk field into the filter is not verified here.
2. **Sidecar POST.** `POST /live/route` on the live receiver, with the same bearer token and the same network allow list as `/live/events`. Body is the object below, or `{"turn": "<id>", "observatory": {...}}`.
3. **Log file.** Point `route_log.path` at a JSON-lines file the router already writes. The reader only tails the file. A line is `{"time": <unix seconds>, "turn": "<optional id>", "observatory": {...}}`.

A header such as `X-Observatory-Route` is not visible to the filter. It is not a delivery path.

## Object

Every field is optional except `v`. Unknown keys are dropped. Invalid values become null and do not erase a value already accepted. Strings are length-capped. This object never carries prompts, arguments, tool results, URLs, session ids, or keys.

```json
{
  "v": 1,
  "turn": "optional chat or message id",
  "decision": {
    "target": "local | external | direct",
    "label": "short label, at most 40 characters",
    "reason": {"code": "lower_snake", "text": "short note, hidden when text is off and in Post mode"},
    "decide_ms": 0.3,
    "upstream_ms": 2.8
  },
  "classifier": {
    "result": "local | external | skipped | not_eligible | disabled | limit | error",
    "why": "lower_snake",
    "ms": 216.1,
    "flags": ["lower_snake"]
  },
  "steps": [
    {"id": "s1", "kind": "agent | llm | tool | mcp", "name": "short name", "server": "mcp only",
     "where": "local | external", "status": "started | ok | error",
     "start_ms": 12, "end_ms": 980, "in_tokens": 1200, "out_tokens": 85}
  ]
}
```

`reason.code` and `classifier.why` match `[a-z0-9_]{1,32}`. Flag names match `[a-z_]{1,32}`, at most 12. `server` is kept only for `kind: mcp`. At most 60 steps. Times are milliseconds from when the router received the request, at most 24 hours. `reason_labels` in the dashboard config can map a code to a short public phrase. The example config ships an empty map.

When `turn` is missing, the receiver attaches the event only if exactly one live turn covers that moment (from 2 seconds before it started until 20 seconds after it ended). Two candidates are not matched. A log line is always labelled "router log (matched by time)". A sidecar matched without an id is labelled "router sidecar (matched by time)". A chunk on the response stream is "router stream".

## What the filter reports without a router

The filter also sends, when Open WebUI gives it the data:

- Built-in flags and counts: web search, image generation, code interpreter, memory, file count, tool id count. These have no timing.
- `owned_by`: `ollama` is a local connection, anything else present is an external connection, as Open WebUI reports it.
- Background task names (`query_generation` and similar), with no prompt text.
- Native tool-call names and the time the filter saw them, labelled "as seen by Open WebUI".
- Ollama `usage` durations (nanoseconds, converted to milliseconds) and token counts when a final chunk includes them.
- A source count and tool names from the outlet, names only.

None of those are verified against a running Open WebUI in this repository. If a field never arrives, the hop is not drawn.
