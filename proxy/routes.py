"""FastAPI route handlers."""

from __future__ import annotations
import time
import uuid

from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .config import (
    get_force_default_model,
    LISTEN_HOST,
    LISTEN_PORT,
    MAX_RETRIES,
    MODEL_CONTEXT_WINDOW,
    PAYLOAD_DIR,
    PROVIDER,
    SAVE_PAYLOAD_FILES,
    get_chat_url,
    get_messages_url,
    get_default_model,
    get_logger,
    provider_is_openai_compatible,
)
from .proxy import ProxyCore
from .translation import prepare_chat_body, prepare_messages_body, openai_response_to_anthropic, openai_sse_to_anthropic_sse
from .utils import dumps, loads, request_id, ws_request_id
from . import prettylog as pl

log = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Generic custom/freeform tool adaptation
# ---------------------------------------------------------------------------

_CUSTOM_TOOL_TYPES = frozenset({"custom", "freeform"})


def _atlas_custom_tool_map(tools):
    """Return request-local metadata for Codex custom/freeform tools."""
    result = {}
    if not isinstance(tools, list):
        return result

    for tool in tools:
        if not isinstance(tool, dict):
            continue

        name = tool.get("name")
        tool_type = tool.get("type")

        if name and tool_type in _CUSTOM_TOOL_TYPES:
            result[name] = {
                "type": tool_type,
                "name": name,
            }

        # Some Codex/OpenAI hosted-tool payloads use a bare type without name.
        if tool_type == "apply_patch" and not name:
            result["apply_patch"] = {
                "type": "custom",
                "name": "apply_patch",
            }

    return result


def _atlas_unwrap_custom_tool_args(raw):
    """Undo Atlas' internal JSON transport wrapper for custom/freeform tools."""
    if not isinstance(raw, str):
        return raw or ""

    try:
        parsed = loads(raw)
    except Exception:
        return raw

    if isinstance(parsed, dict) and "input" in parsed:
        value = parsed["input"]
        return value if isinstance(value, str) else str(value)

    return raw


def _atlas_custom_tool_kind(name, custom_tools):
    """Return original tool type, or None for an ordinary function tool."""
    meta = custom_tools.get(name)
    if isinstance(meta, dict):
        return meta.get("type")
    return None


# ---------------------------------------------------------------------------
# Responses API `additional_tools` namespace groups
# ---------------------------------------------------------------------------

_ATLAS_DEFAULT_NAMESPACE = "functions"


def _atlas_flatten_namespace_tools(items, existing_names=None):
    """Flatten Responses API `additional_tools` namespace groups.

    Codex's newer model families (gpt-6 and the *-sol variants) declare
    their tool catalogue inside an `additional_tools` input item, using
    namespace groups, instead of a top-level `tools` array:

        {"type": "additional_tools", "role": "developer",
         "tools": [{"type": "namespace", "name": "functions",
                    "tools": [{"type": "custom", "name": "exec", ...}]}]}

    The upstream chat/completions endpoint has no notion of namespaces, so
    every child tool is hoisted into a flat tool definition. The namespace
    is remembered so the response can be tagged the way Codex expects
    (Codex resolves `ToolName::new(namespace, name)` against its registry).

    Returns (tools, meta) where `tools` is a list of tool definitions in
    the same shape Codex uses for top-level tools and `meta` maps the
    upstream tool name to {"name": original name, "namespace": namespace}.
    """
    groups = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("type") != "additional_tools":
            continue
        declared = item.get("tools")
        if not isinstance(declared, list):
            continue
        for group in declared:
            if not isinstance(group, dict) or group.get("type") != "namespace":
                continue
            namespace = group.get("name") or _ATLAS_DEFAULT_NAMESPACE
            children = group.get("tools")
            if not isinstance(children, list):
                continue
            for child in children:
                if isinstance(child, dict) and child.get("name"):
                    groups.append((namespace, child))

    if not groups:
        return [], {}

    # The same bare name may be reused by more than one namespace. The
    # upstream request needs unique function names, so qualify the
    # non-default namespaces whenever that happens.
    name_namespaces = {}
    for namespace, child in groups:
        name_namespaces.setdefault(child["name"], set()).add(namespace)

    taken = set(existing_names or ())
    tools = []
    meta = {}
    for namespace, child in groups:
        name = child["name"]
        upstream = name
        if len(name_namespaces[name]) > 1 and namespace != _ATLAS_DEFAULT_NAMESPACE:
            upstream = f"{namespace}__{name}"
        suffix = 2
        while upstream in taken:
            upstream = f"{namespace}__{name}__{suffix}"
            suffix += 1
        taken.add(upstream)

        tool = dict(child)
        tool["name"] = upstream
        tools.append(tool)
        meta[upstream] = {
            "name": name,
            "namespace": namespace,
            "type": child.get("type"),
        }

    return tools, meta


def _atlas_tool_meta(tools, namespace_meta=None):
    """Request-local metadata for every tool we advertise upstream.

    Maps the upstream tool name to {"name", "namespace", "type"} so the
    Responses API response can be tagged the way Codex expects.
    """
    meta = {}
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not name:
            continue
        meta[name] = {"name": name, "namespace": None, "type": tool.get("type")}

    for upstream, info in (namespace_meta or {}).items():
        meta[upstream] = info

    return meta


def _atlas_upstream_tool_name(name, namespace, meta):
    """Map a replayed tool name/namespace back to the upstream tool name.

    Codex replays prior assistant tool calls in the next request using the
    name and namespace we returned. The upstream tool name is usually the
    same, but it differs when two namespaces share a bare tool name.
    """
    if not name:
        return name

    if namespace:
        for upstream, info in meta.items():
            if info.get("namespace") == namespace and info.get("name") == name:
                return upstream

    matches = [up for up, info in meta.items() if info.get("name") == name]
    if len(matches) == 1:
        return matches[0]
    if name in meta:
        return name

    return name


def _atlas_tool_call_info(name, tool_meta, custom_tools):
    """Resolve an upstream tool call into (display name, namespace, kind).

    The Responses API response must carry the tool name Codex advertised
    (not the flattened upstream name) plus the namespace it advertised it
    under, otherwise Codex cannot resolve the call in its registry.

    `kind` is the original tool type: "custom"/"freeform" for freeform
    tools (which Codex expects as `custom_tool_call` with a raw string
    `input`), None for ordinary function tools.
    """
    info = (tool_meta or {}).get(name)
    if isinstance(info, dict):
        display = info.get("name") or name
        namespace = info.get("namespace")
        kind = info.get("type")
        if kind in _CUSTOM_TOOL_TYPES:
            return display, namespace, kind
        return display, namespace, _atlas_custom_tool_kind(name, custom_tools)

    return name, None, _atlas_custom_tool_kind(name, custom_tools)


def _atlas_tool_call_item(state, status="completed", include_args=True):
    """Build a Responses API output item for a tool call.

    Freeform ("custom") tools must be emitted as `custom_tool_call` with a
    raw string `input`; ordinary tools are emitted as `function_call` with
    JSON `arguments`. Codex rejects a mismatched pair ("tool exec invoked
    with incompatible payload"), so the original tool type decides.

    The `namespace` field is only meaningful for tools Codex advertised in
    an `additional_tools` namespace group; it is what Codex resolves the
    call against (`ToolName::new(namespace, name)`).
    """
    custom = state.get("custom_kind") in _CUSTOM_TOOL_TYPES
    name = state.get("name") or state.get("upstream_name") or ""
    namespace = state.get("namespace")

    item: Dict[str, Any] = {
        "id": f"{'ctc' if custom else 'fc'}_{state['id']}",
        "call_id": state["id"],
        "type": "custom_tool_call" if custom else "function_call",
        "status": status,
        "name": name,
    }

    if namespace:
        item["namespace"] = namespace

    if custom:
        item["input"] = (
            _atlas_unwrap_custom_tool_args(state.get("args", ""))
            if include_args
            else ""
        )
    else:
        item["arguments"] = state.get("args", "") if include_args else ""

    return item


# Set by main.py during lifespan startup
proxy: Optional[ProxyCore] = None


@router.get("/")
async def root() -> Dict[str, Any]:
    assert proxy is not None
    return {
        "service": "OpenRouter Translation Proxy",
        "version": "1.1.0",
        "provider": PROVIDER,
        "default_model": get_default_model(),
        "force_default_model": get_force_default_model(),
        "endpoints": [
            "POST /v1/chat/completions",
            "POST /v1/messages",
            "GET  /v1/models",
            "GET  /health",
            "GET  /health/keys",
        ],
        "keys_loaded": proxy.pool.stats()["total"],
    }


@router.get("/health")
async def health() -> Dict[str, Any]:
    assert proxy is not None
    return {
        "status": "ok",
        "keys": proxy.pool.stats(),
        "listen": f"{LISTEN_HOST}:{LISTEN_PORT}",
    }


@router.get("/health/keys")
async def health_keys() -> Dict[str, Any]:
    """Detailed per-key statistics (no secret material)."""
    assert proxy is not None
    return {
        "status": "ok",
        "summary": proxy.pool.stats(),
        "keys": proxy.pool.detailed_stats(),
    }


@router.get("/stats")
async def stats() -> Dict[str, Any]:
    """Legacy /stats endpoint for atlas CLI compatibility."""
    assert proxy is not None
    stats = proxy.pool.stats()
    return {
        "total_keys": stats["total"],
        "healthy_keys": stats["healthy"],
        "cooling_keys": stats["cooling"],
        "suspended_keys": stats["suspended"],
    }




@router.get("/v1/models")
@router.get("/models")
async def models(request: Request) -> Response:
    """Return Anthropic-shaped model IDs so Claude Code UI accepts them.
    Actual inference always uses the provider's default model via get_force_default_model().
    """
    rid = request_id(request)
    now = int(time.time())
    default_model = get_default_model()
    data = {
        "object": "list",
        "data": [
            {
                "id": "claude-opus-5",
                "object": "model",
                "created": now,
                "owned_by": "anthropic",
                "display_name": "Opus 5",
                "type": "model",
                "context_window": MODEL_CONTEXT_WINDOW,
            },
            {
                "id": "claude-opus-5[1m]",
                "object": "model",
                "created": now,
                "owned_by": "anthropic",
                "display_name": "Opus 5 (1M context)",
                "type": "model",
                "context_window": MODEL_CONTEXT_WINDOW,
            },
            {
                "id": "claude-sonnet-5",
                "object": "model",
                "created": now,
                "owned_by": "anthropic",
                "display_name": "Sonnet 5",
                "type": "model",
                "context_window": MODEL_CONTEXT_WINDOW,
            },
            {
                "id": "claude-sonnet-5[1m]",
                "object": "model",
                "created": now,
                "owned_by": "anthropic",
                "display_name": "Sonnet 5 (1M context)",
                "type": "model",
                "context_window": MODEL_CONTEXT_WINDOW,
            },
            {
                "id": "claude-haiku-4-5",
                "object": "model",
                "created": now,
                "owned_by": "anthropic",
                "display_name": "Haiku 4.5",
                "type": "model",
                "context_window": MODEL_CONTEXT_WINDOW,
            },
            {
                "id": default_model,
                "object": "model",
                "created": now,
                "owned_by": PROVIDER,
                "display_name": default_model,
                "type": "model",
                "context_window": MODEL_CONTEXT_WINDOW,
            },
        ],
    }
    return JSONResponse(data, headers={"x-request-id": rid})



@router.post("/v1/responses")
async def responses_api(request: Request) -> Response:
    """OpenAI Responses API endpoint - translates chat/completions SSE into
    proper Responses API SSE events (response.created / output_item /
    content_part / output_text.delta / .done / response.completed)."""
    assert proxy is not None
    rid = request_id(request)
    try:
        body = loads(await request.body())
    except Exception:
        return JSONResponse(
            {"error": {"message": "invalid json", "type": "invalid_request_error"}},
            status_code=400,
            headers={"x-request-id": rid},
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {"error": {"message": "body must be object", "type": "invalid_request_error"}},
            status_code=400,
            headers={"x-request-id": rid},
        )


    # Tool metadata for the Responses API response side. Codex advertises
    # tools either as a top-level `tools` array (GPT-5 family) or inside
    # `additional_tools` namespace groups (GPT-6 / *-sol family). Both are
    # flattened into a single upstream tool list, and this map remembers the
    # original name/namespace/type so the response can be tagged the way
    # Codex expects to resolve it.
    tool_meta: Dict[str, Any] = {}
    namespace_meta: Dict[str, Any] = {}

    # Convert Responses API shape (input/instructions) to chat messages
    if "input" in body or "instructions" in body:
        msgs = []
        instructions = body.pop("instructions", None)
        if instructions:
            msgs.append({"role": "system", "content": instructions})
        raw_input = body.pop("input", []) or []

        # Codex's newer model families (gpt-6 / *-sol) declare their tool
        # catalogue inside an `additional_tools` input item using namespace
        # groups instead of a top-level `tools` array. The upstream
        # chat/completions endpoint knows nothing about namespaces, so the
        # children are flattened into plain function tools here and the
        # namespace is remembered for the response side.
        existing_tools = body.get("tools")
        if not isinstance(existing_tools, list):
            existing_tools = []
        existing_names = {
            t.get("name")
            for t in existing_tools
            if isinstance(t, dict) and t.get("name")
        }
        namespace_tools, namespace_meta = _atlas_flatten_namespace_tools(
            raw_input, existing_names
        )
        if namespace_tools:
            body["tools"] = existing_tools + namespace_tools
        tool_meta = _atlas_tool_meta(body.get("tools"), namespace_meta)

        for item in raw_input:
            itype = item.get("type")

            if itype == "additional_tools":
                # Tool catalogue, not conversation content. Already
                # flattened into body["tools"] above.
                continue

            if itype == "function_call":
                msgs.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": item.get("call_id"),
                        "type": "function",
                        "function": {
                            "name": _atlas_upstream_tool_name(
                                item.get("name"),
                                item.get("namespace"),
                                tool_meta,
                            ),
                            "arguments": item.get("arguments", ""),
                        },
                    }],
                })
                continue

            if itype == "function_call_output":
                msgs.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id"),
                    "content": item.get("output", ""),
                })
                continue

            # Codex uses custom_tool_call (freeform tools, e.g. apply_patch).
            #
            # The synthesized tool schema in translation.py declares a single
            # `input` field, so arguments MUST be a JSON-encoded object like
            # {"input": "<patch text>"} when the round-tripped assistant
            # message reaches OpenRouter. Codex stores the raw patch in
            # item["input"] -- we wrap it in the schema-shaped object and
            # JSON-encode the whole thing so chat/completions parses it.
            #
            # If the stored input already looks like a JSON object (legacy
            # function-style custom tools), pass it through JSON-encoded as-is.
            if itype == "custom_tool_call":
                import json as _json
                raw_input = item.get("input", "")
                if isinstance(raw_input, str) and raw_input.strip().startswith("{"):
                    try:
                        parsed = _json.loads(raw_input)
                        if isinstance(parsed, dict):
                            args_str = raw_input
                        else:
                            args_str = _json.dumps({"input": raw_input})
                    except Exception:
                        args_str = _json.dumps({"input": raw_input})
                else:
                    args_str = _json.dumps({"input": raw_input})
                msgs.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": item.get("call_id"),
                        "type": "function",
                        "function": {
                            "name": _atlas_upstream_tool_name(
                                item.get("name"),
                                item.get("namespace"),
                                namespace_meta,
                            ),
                            "arguments": args_str,
                        },
                    }],
                })
                continue

            if itype == "custom_tool_call_output":
                msgs.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id"),
                    "content": item.get("output", ""),
                })
                continue

            # OpenAI hosted apply_patch tool wire format.
            # The diff payload lives at item["input"] on the Responses API.
            # Older payloads (and some forks) used item["operation"]["diff"];
            # fall back to that so we do not break previously-recorded
            # conversation history.
            #
            # arguments must be a JSON-encoded object {"input": "<diff>"} to
            # match the synthesized schema in translation.py. If the stored
            # payload already looks like a JSON object, pass it through as-is.
            if itype == "apply_patch_call":
                import json as _json
                raw = item.get("input")
                if raw is None:
                    raw = item.get("operation", {}).get("diff", "")
                if isinstance(raw, str) and raw.strip().startswith("{"):
                    args_str = raw  # already a JSON object string
                else:
                    args_str = _json.dumps({"input": raw})
                msgs.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": item.get("call_id"),
                        "type": "function",
                        "function": {
                            "name": "apply_patch",
                            "arguments": args_str,
                        },
                    }],
                })
                continue

            if itype == "apply_patch_call_output":
                msgs.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id"),
                    "content": item.get("output", ""),
                })
                continue

            role = item.get("role", "user")
            if role == "developer":
                role = "system"
            parts = item.get("content", [])
            text = "\n".join(
                p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")
            )
            if text:
                msgs.append({"role": role, "content": text})
        if msgs:
            body["messages"] = msgs

    # Responses-only structured output (title gen etc) - drop, chat/completions
    # doesn't support text.format json_schema the same way
    body.pop("text", None)
    body.pop("store", None)
    body.pop("include", None)

    # Capture the original Codex tool kinds BEFORE prepare_chat_body()
    # converts custom/freeform tools into OpenAI function tools.
    original_custom_tools = _atlas_custom_tool_map(body.get("tools"))

    body = prepare_chat_body(body)
    stream = bool(body.get("stream", False))
    payload = dumps(body)
    _payload_path = _dump_payload(rid, "", body)

    tr = pl.trace(rid)
    tr.start("openai", body.get("model"), "responses", stream)

    resp = await proxy.forward(
        "POST",
        get_chat_url(),
        body=payload,
        stream=stream,
        request_id=rid,
    )

    if not stream or not hasattr(resp, "body_iterator"):
        return resp

    response_id = f"resp_{rid}"
    item_id = f"msg_{rid}"
    model_name = body.get("model")

    async def translate_stream():
        text_so_far = []
        started_item = False
        buf = b""
        tool_call_state = {}

        yield _sse_event("response.created", {
            "type": "response.created",
            "response": {
                "id": response_id,
                "object": "response",
                "status": "in_progress",
                "model": model_name,
            },
        })

        async for raw in resp.body_iterator:
            if not raw:
                continue
            chunk = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8")
            buf += chunk

            # Split on double-newline SSE frame boundaries; keep remainder buffered
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                data_line = None
                for line in frame.split(b"\n"):
                    line = line.strip()
                    if line.startswith(b"data:"):
                        data_line = line[5:].strip()
                        break
                if data_line is None:
                    continue
                if data_line == b"[DONE]":
                    continue
                try:
                    data = loads(data_line)
                except Exception:
                    continue

                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {}) or {}

                tool_calls = delta.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        idx = tc.get("index", 0)
                        call_id = tc.get("id")
                        fn = tc.get("function", {}) or {}
                        name = fn.get("name")
                        args_piece = fn.get("arguments", "")

                        if idx not in tool_call_state:
                            tool_call_state[idx] = {
                                "id": call_id or f"call_{rid}_{idx}",
                                "name": "",
                                "upstream_name": name or "",
                                "namespace": None,
                                "args": "",
                                "custom_kind": None,
                            }

                        state = tool_call_state[idx]

                        # The upstream tool name may be a flattened
                        # namespace-qualified name. Remember the original
                        # name/namespace/type so the Responses API response
                        # can be tagged the way Codex resolves it.
                        if name and not state["upstream_name"]:
                            state["upstream_name"] = name
                        if name:
                            disp, ns, kind = _atlas_tool_call_info(
                                name, tool_meta, original_custom_tools
                            )
                            state["name"] = disp
                            state["namespace"] = ns
                            state["custom_kind"] = kind

                        if not state.get("added"):
                            state["added"] = True
                            item = _atlas_tool_call_item(
                                state,
                                status="in_progress",
                                include_args=False,
                            )
                            yield _sse_event("response.output_item.added", {
                                "type": "response.output_item.added",
                                "output_index": idx + 1,
                                "item": item,
                            })

                        if args_piece:
                            state["args"] += args_piece

                            if state["custom_kind"] not in _CUSTOM_TOOL_TYPES:
                                yield _sse_event("response.function_call_arguments.delta", {
                                    "type": "response.function_call_arguments.delta",
                                    "item_id": state["id"],
                                    "output_index": idx + 1,
                                    "delta": args_piece,
                                })

                content = delta.get("content")
                if content:
                    if not started_item:
                        yield _sse_event("response.output_item.added", {
                            "type": "response.output_item.added",
                            "output_index": 0,
                            "item": {
                                "id": item_id,
                                "type": "message",
                                "role": "assistant",
                                "status": "in_progress",
                                "content": [],
                            },
                        })
                        yield _sse_event("response.content_part.added", {
                            "type": "response.content_part.added",
                            "item_id": item_id,
                            "output_index": 0,
                            "content_index": 0,
                            "part": {"type": "output_text", "text": ""},
                        })
                        started_item = True

                    text_so_far.append(content)
                    yield _sse_event("response.output_text.delta", {
                        "type": "response.output_text.delta",
                        "item_id": item_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": content,
                    })

        full_text = "".join(text_so_far)

        for idx, tc in tool_call_state.items():
            if tc.get("custom_kind") in _CUSTOM_TOOL_TYPES:
                custom_input = _atlas_unwrap_custom_tool_args(tc["args"])

                yield _sse_event("response.custom_tool_call_arguments.done", {
                    "type": "response.custom_tool_call_arguments.done",
                    "item_id": tc["id"],
                    "output_index": idx + 1,
                    "input": custom_input,
                })

                yield _sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": idx + 1,
                    "item": _atlas_tool_call_item(tc),
                })
            else:
                yield _sse_event("response.function_call_arguments.done", {
                    "type": "response.function_call_arguments.done",
                    "item_id": tc["id"],
                    "output_index": idx + 1,
                    "arguments": tc["args"],
                })
                yield _sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": idx + 1,
                    "item": _atlas_tool_call_item(tc),
                })

        if started_item:
            yield _sse_event("response.output_text.done", {
                "type": "response.output_text.done",
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "text": full_text,
            })
            yield _sse_event("response.content_part.done", {
                "type": "response.content_part.done",
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": full_text},
            })
            yield _sse_event("response.output_item.done", {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": full_text}],
                },
            })

        final_output = []
        for idx, tc in tool_call_state.items():
            final_output.append(_atlas_tool_call_item(tc))
        if started_item:
            final_output.append({
                "id": item_id,
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": full_text}],
            })

        yield _sse_event("response.completed", {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "object": "response",
                "status": "completed",
                "model": model_name,
                "output": final_output,
            },
        })

    return StreamingResponse(translate_stream(), media_type="text/event-stream")



@router.post("/v1/chat/completions")
@router.post("/chat/completions")
async def chat_completions(request: Request) -> Response:
    assert proxy is not None
    rid = request_id(request)
    try:
        body = loads(await request.body())
    except Exception:
        return JSONResponse(
            {
                "error": {
                    "message": "invalid json",
                    "type": "invalid_request_error",
                }
            },
            status_code=400,
            headers={"x-request-id": rid},
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {
                "error": {
                    "message": "body must be object",
                    "type": "invalid_request_error",
                }
            },
            status_code=400,
            headers={"x-request-id": rid},
        )

    body = prepare_chat_body(body)
    stream = bool(body.get("stream", False))
    payload = dumps(body)

    _payload_path = _dump_payload(rid, "", body)

    tr = pl.trace(rid)  # logging-only lifecycle trace
    tr.start("openai", body.get("model"), "chat/completions", stream)

    resp = await proxy.forward(
        "POST",
        get_chat_url(),
        body=payload,
        stream=stream,
        request_id=rid,
    )
    return resp

@router.post("/v1/messages")
@router.post("/messages")
async def messages(request: Request) -> Response:
    assert proxy is not None
    rid = request_id(request)
    try:
        body = loads(await request.body())
    except Exception:
        return JSONResponse(
            {
                "error": {
                    "message": "invalid json",
                    "type": "invalid_request_error",
                }
            },
            status_code=400,
            headers={"x-request-id": rid},
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {
                "error": {
                    "message": "body must be object",
                    "type": "invalid_request_error",
                }
            },
            status_code=400,
            headers={"x-request-id": rid},
        )

    body = prepare_messages_body(body) if not provider_is_openai_compatible() else prepare_chat_body(body)
    stream = bool(body.get("stream", False))
    payload = dumps(body)

    _payload_path = _dump_payload(rid, "_messages", body)

    tr = pl.trace(rid)  # logging-only lifecycle trace
    tr.start("anthropic", body.get("model"), "messages", stream)

    extra: Dict[str, str] = {}
    if "anthropic-version" in request.headers:
        extra["anthropic-version"] = request.headers["anthropic-version"]
    else:
        extra["anthropic-version"] = "2023-06-01"
    if "anthropic-beta" in request.headers:
        extra["anthropic-beta"] = request.headers["anthropic-beta"]

    upstream_url = get_chat_url() if provider_is_openai_compatible() else get_messages_url()
    resp = await proxy.forward(
        "POST",
        upstream_url,
        body=payload,
        extra_headers=extra,
        stream=stream,
        request_id=rid,
    )

    # HF provider: convert OpenAI response → Anthropic /messages shape
    # HF/NVIDIA: convert OpenAI response → Anthropic /messages shape
    if provider_is_openai_compatible():
        if not stream and isinstance(resp, Response):
            openai_data = loads(resp.body)
            anthropic_data = openai_response_to_anthropic(openai_data, rid=rid)
            new_body = dumps(anthropic_data)
            return Response(
                content=new_body,
                status_code=resp.status_code,
                media_type="application/json",
                headers={k: v for k, v in resp.headers.items() if k.lower() != "content-length"},
            )
        # Streaming: convert OpenAI SSE → Anthropic SSE
        if stream:
            return await _stream_openai_to_anthropic(
                rid, upstream_url, payload, resp
            )

    return resp



async def _stream_openai_to_anthropic(rid: str, upstream_url: str, payload: bytes, resp: Response) -> StreamingResponse:
    """Stream an OpenAI SSE response from HF and re-emit as Anthropic SSE."""
    async def translate_stream():
        # Yield message_start + content_block_start scaffolding
        yield _sse_event("message_start", {
            "type": "message_start",
            "message": {
                "id": rid,
                "type": "message",
                "role": "assistant",
                "model": get_default_model(),
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })

        yield _sse_event("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })

        # Keep block lifecycle state across OpenAI SSE chunks.
        translation_state = {
            "thinking_started": False,
            "tool_blocks": {},
        }

        # Stream from upstream (proxy.iter_upstream_sse or proxy.forward already returned a StreamingResponse)
        async for raw in resp.body_iterator:
            if not raw:
                continue
            frame = raw.strip()
            if not frame:
                continue
            data_line = None
            for line in frame.split(b"\n"):
                if line.startswith(b"data:"):
                    data_line = line[5:].strip()
                    break
            if data_line is None or data_line == b"[DONE]":
                continue
            try:
                chunk = loads(data_line)
            except Exception:
                continue

            # HF SSE per-chunk diagnostics (debug only — too noisy for INFO)
            try:
                log.debug(
                    "req=%s HF_SSE_CHUNK %s",
                    rid,
                    data_line.decode("utf-8", errors="replace")[:4000],
                )
            except Exception:
                pass

            # Emit Anthropic events for each OpenAI delta
            for evt_name, evt_data in openai_sse_to_anthropic_sse(
                chunk,
                state=translation_state,
            ):
                if evt_data is None:
                    continue
                yield _sse_event(evt_name, evt_data)

        # Close events
        yield _sse_event("content_block_stop", {
            "type": "content_block_stop",
            "index": 0,
        })
        yield _sse_event("message_stop", {
            "type": "message_stop",
        })

    return StreamingResponse(
        translate_stream(),
        media_type="text/event-stream",
        headers={"x-request-id": rid},
    )


def _sse_event(event: str, data: dict) -> bytes:
    raw = dumps(data)
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return f"event: {event}\ndata: {raw}\n\n".encode("utf-8")


def _dump_payload(rid: str, tag: str, body: Any) -> str:
    """Write the request payload to PAYLOAD_DIR when SAVE_PAYLOAD_FILES is on.

    Returns the path written ("" when disabled or on failure). Never raises.
    """
    if not SAVE_PAYLOAD_FILES:
        return ""
    import os
    import orjson

    os.makedirs(PAYLOAD_DIR, exist_ok=True)
    path = os.path.join(PAYLOAD_DIR, f"payload_{rid}{tag}.json")
    try:
        with open(path, "w") as f:
            f.write(orjson.dumps(body, option=orjson.OPT_INDENT_2).decode())
        log.info("req=%s payload_saved %s (%d bytes)", rid, path, os.path.getsize(path))
    except Exception:
        return ""
    return path


