from __future__ import annotations

import os
from typing import Any


def build_workflow_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw_messages = payload.get("messages")
    if isinstance(raw_messages, list):
        collected: list[dict[str, str]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if role == "" or content == "":
                continue
            collected.append({"role": role, "content": content})
        if len(collected) > 0:
            return collected

    fallback = str(payload.get("message", "")).strip()
    if fallback == "":
        return [{"role": "user", "content": "Say hello and ask how to help."}]
    return [{"role": "user", "content": fallback}]


def workflow_text_output(terminal_output: Any) -> str:
    if terminal_output is None:
        return ""
    if isinstance(terminal_output, str):
        return terminal_output
    if isinstance(terminal_output, dict):
        subject = terminal_output.get("subject")
        body = terminal_output.get("body")
        if isinstance(subject, str) and isinstance(body, str):
            return f"Subject: {subject}\n\n{body}"
    return str(terminal_output)


def extract_workflow_nerdstats(
    workflow_result: dict[str, Any],
) -> dict[str, Any] | None:
    events = workflow_result.get("events")
    if not isinstance(events, list):
        return None

    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type", "")).strip()
        if event_type != "workflow_completed":
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        nerdstats = metadata.get("nerdstats")
        if isinstance(nerdstats, dict):
            return nerdstats
    return None


def count_workflow_events_by_type(workflow_result: dict[str, Any]) -> dict[str, int]:
    events = workflow_result.get("events")
    if not isinstance(events, list):
        return {}

    counts: dict[str, int] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type", "")).strip()
        if event_type == "":
            continue
        prior = counts.get(event_type, 0)
        counts[event_type] = prior + 1
    return counts


def build_trace_url(trace_id: str) -> str:
    normalized_trace_id = trace_id.strip()
    if normalized_trace_id == "":
        return ""
    base_url = os.getenv("TRACE_UI_BASE_URL", "http://localhost:16686").strip()
    normalized_base_url = base_url.rstrip("/")
    if normalized_base_url == "":
        normalized_base_url = "http://localhost:16686"
    return f"{normalized_base_url}/trace/{normalized_trace_id}"


def build_chat_message_content(
    terminal_output: Any, workflow_result: dict[str, Any]
) -> dict[str, Any]:
    return {
        "reply": workflow_text_output(terminal_output),
        "terminal_output": terminal_output,
        "workflow": {
            "workflow_id": workflow_result.get("workflow_id"),
            "terminal_node": workflow_result.get("terminal_node"),
        },
    }


def build_chat_message_metadata(
    req: Any, workflow_result: dict[str, Any]
) -> dict[str, Any]:
    trace_id = str(req.trace.trace_id).strip()
    metadata: dict[str, Any] = {
        "source": "runtime.workflow.invoke",
        "workflow_id": workflow_result.get("workflow_id"),
        "terminal_node": workflow_result.get("terminal_node"),
        "trace": workflow_result.get("trace", []),
        "step_timings": workflow_result.get("step_timings", []),
        "event_counts": count_workflow_events_by_type(workflow_result),
        "nerdstats": extract_workflow_nerdstats(workflow_result),
        "llm_node_metrics": workflow_result.get("llm_node_metrics", {}),
        "total_elapsed_ms": workflow_result.get("total_elapsed_ms"),
        "trace_context": {
            "trace_id": trace_id,
            "span_id": str(req.trace.span_id).strip(),
            "tenant_id": str(req.trace.tenant_id).strip(),
            "trace_url": build_trace_url(trace_id),
        },
    }
    events = workflow_result.get("events")
    if isinstance(events, list):
        metadata["events"] = events
    return metadata


def coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None
    text = str(value).strip()
    if text == "":
        return None
    if text.isdigit():
        return int(text)
    return None


def token_metrics_from_nerdstats(nerdstats: Any) -> dict[str, int]:
    if not isinstance(nerdstats, dict):
        return {}
    total_tokens = coerce_int(nerdstats.get("total_tokens"))
    prompt_tokens = coerce_int(nerdstats.get("total_input_tokens"))
    completion_tokens = coerce_int(nerdstats.get("total_output_tokens"))

    metrics: dict[str, int] = {}
    if prompt_tokens is not None and prompt_tokens >= 0:
        metrics["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None and completion_tokens >= 0:
        metrics["completion_tokens"] = completion_tokens
    if total_tokens is not None and total_tokens >= 0:
        metrics["total_tokens"] = total_tokens
    elif (
        "prompt_tokens" in metrics
        and "completion_tokens" in metrics
        and metrics["prompt_tokens"] >= 0
        and metrics["completion_tokens"] >= 0
    ):
        metrics["total_tokens"] = (
            metrics["prompt_tokens"] + metrics["completion_tokens"]
        )
    return metrics


def resolve_chat_id(input_payload: dict[str, Any], fallback_run_id: str) -> str:
    for candidate in (
        input_payload.get("chat_id"),
        input_payload.get("chatId"),
        input_payload.get("conversation_id"),
        input_payload.get("conversationId"),
    ):
        chat_id = str(candidate).strip()
        if chat_id != "":
            return chat_id
    return fallback_run_id
