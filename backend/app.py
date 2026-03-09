from __future__ import annotations

import logging
import os
import time
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from simpleflow_sdk import (
    ChatMessageWrite,
    InvokeTokenVerifier,
    QueueContract,
    RuntimeEvent,
    SimpleFlowAuthenticationError,
    SimpleFlowAuthorizationError,
    SimpleFlowClient,
    SimpleFlowLifecycleError,
)


class InvokeTrace(BaseModel):
    trace_id: str
    span_id: str
    tenant_id: str


class InvokeRequest(BaseModel):
    schema_version: str
    run_id: str
    agent_id: str
    agent_version: str
    mode: str
    trace: InvokeTrace
    input: dict[str, Any]
    deadline_ms: int = 0
    idempotency_key: str = ""


class QueueMessage(BaseModel):
    message_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class InvokeScope:
    agent_id: str = ""
    org_id: str = ""
    user_id: str = ""
    run_id: str = ""


@dataclass(slots=True)
class InvokeTrustConfig:
    enabled: bool
    issuer: str
    audience: str
    signing_key: str
    jwks_url: str


logger = logging.getLogger("simpleflow.runtime.template")


app = FastAPI(title="SimpleFlow Python Runtime Template")

cors_allow_origins_raw = os.getenv(
    "RUNTIME_CORS_ALLOW_ORIGINS",
    "http://localhost:5175,http://localhost:5173",
).strip()
cors_allow_origins = [
    origin.strip()
    for origin in cors_allow_origins_raw.split(",")
    if origin.strip() != ""
]
if len(cors_allow_origins) == 0:
    cors_allow_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

queue_buffer: deque[QueueMessage] = deque()

agent_id = os.getenv("RUNTIME_AGENT_ID", "sample-python-runtime")
agent_version = os.getenv("RUNTIME_AGENT_VERSION", "v1")

api_base_url = os.getenv("SIMPLEFLOW_API_BASE_URL", "").strip()
api_token = os.getenv("SIMPLEFLOW_API_TOKEN", "").strip()
machine_client_id = os.getenv("SIMPLEFLOW_CLIENT_ID", "").strip()
machine_client_secret = os.getenv("SIMPLEFLOW_CLIENT_SECRET", "").strip()
sdk_client = (
    SimpleFlowClient(
        api_base_url,
        api_token=api_token,
        oauth_client_id=machine_client_id,
        oauth_client_secret=machine_client_secret,
    )
    if api_base_url
    else None
)

workflow_root = Path(
    os.getenv(
        "WORKFLOW_ROOT",
        str((Path(__file__).resolve().parent.parent / "workflows").resolve()),
    )
)
workflow_entry_file = os.getenv(
    "WORKFLOW_ENTRY_FILE", "email-chat-orchestrator-with-subgraph-tool.yaml"
).strip()
workflow_path = workflow_root / workflow_entry_file
simple_agents_provider = (
    os.getenv("SIMPLE_AGENTS_PROVIDER", "openai").strip() or "openai"
)
simple_agents_api_base = os.getenv("CUSTOM_API_BASE", "").strip()
simple_agents_api_key = os.getenv("CUSTOM_API_KEY", "").strip()


def _agent_key(runtime_agent_id: str, runtime_agent_version: str) -> str:
    return f"{runtime_agent_id.strip()}::{runtime_agent_version.strip()}"


def _build_agent_catalog() -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    primary = {
        "agent_id": agent_id,
        "agent_version": agent_version,
        "runtime_id": os.getenv("RUNTIME_BOOTSTRAP_RUNTIME_ID", "").strip(),
        "endpoint_url": os.getenv("RUNTIME_BOOTSTRAP_ENDPOINT_URL", "").strip(),
        "enabled": True,
    }
    catalog.append(primary)

    raw = os.getenv("RUNTIME_AGENT_CATALOG_JSON", "").strip()
    if raw != "":
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                for item in decoded:
                    if not isinstance(item, dict):
                        continue
                    runtime_agent_id = str(item.get("agent_id", "")).strip()
                    runtime_agent_version = str(item.get("agent_version", "")).strip()
                    if runtime_agent_id == "" or runtime_agent_version == "":
                        continue
                    catalog.append(
                        {
                            "agent_id": runtime_agent_id,
                            "agent_version": runtime_agent_version,
                            "runtime_id": str(item.get("runtime_id", "")).strip(),
                            "endpoint_url": str(item.get("endpoint_url", "")).strip(),
                            "enabled": bool(item.get("enabled", True)),
                        }
                    )
        except json.JSONDecodeError:
            logger.warning("invalid RUNTIME_AGENT_CATALOG_JSON; ignoring value")

    deduped: dict[str, dict[str, Any]] = {}
    for item in catalog:
        key = _agent_key(item["agent_id"], item["agent_version"])
        deduped[key] = item
    return [entry for entry in deduped.values() if bool(entry.get("enabled", True))]


agent_catalog = _build_agent_catalog()
onboarding_states: dict[str, dict[str, Any]] = {}


def env_bool(key: str, fallback: bool = False) -> bool:
    value = os.getenv(key, "").strip().lower()
    if value == "":
        return fallback
    return value in {"1", "true", "yes", "on"}


def _build_invoke_trust_config() -> InvokeTrustConfig:
    enabled = env_bool("RUNTIME_INVOKE_TRUST_ENABLED", False)
    issuer = os.getenv("RUNTIME_INVOKE_TOKEN_ISSUER", "").strip()
    audience = os.getenv("RUNTIME_INVOKE_TOKEN_AUDIENCE", "").strip()
    signing_key = os.getenv("RUNTIME_INVOKE_TOKEN_SIGNING_KEY", "").strip()
    jwks_url = os.getenv("RUNTIME_INVOKE_TOKEN_JWKS_URL", "").strip()

    if not enabled:
        return InvokeTrustConfig(
            enabled=False,
            issuer=issuer,
            audience=audience,
            signing_key=signing_key,
            jwks_url=jwks_url,
        )

    if issuer == "":
        raise ValueError(
            "RUNTIME_INVOKE_TOKEN_ISSUER is required when invoke trust is enabled"
        )
    if audience == "":
        raise ValueError(
            "RUNTIME_INVOKE_TOKEN_AUDIENCE is required when invoke trust is enabled"
        )
    if signing_key == "" and jwks_url == "":
        raise ValueError(
            "set RUNTIME_INVOKE_TOKEN_SIGNING_KEY or RUNTIME_INVOKE_TOKEN_JWKS_URL when invoke trust is enabled"
        )

    return InvokeTrustConfig(
        enabled=True,
        issuer=issuer,
        audience=audience,
        signing_key=signing_key,
        jwks_url=jwks_url,
    )


invoke_trust_config = _build_invoke_trust_config()
shared_key_verifier = (
    InvokeTokenVerifier(
        issuer=invoke_trust_config.issuer,
        audience=invoke_trust_config.audience,
        algorithms=["HS256", "HS384", "HS512"],
    )
    if invoke_trust_config.enabled and invoke_trust_config.signing_key != ""
    else None
)
jwks_client = (
    jwt.PyJWKClient(invoke_trust_config.jwks_url)
    if invoke_trust_config.enabled and invoke_trust_config.jwks_url != ""
    else None
)


def _extract_bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "").strip()
    if authorization == "":
        raise HTTPException(status_code=401, detail="unauthorized invoke request")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].strip().lower() != "bearer":
        raise HTTPException(status_code=401, detail="unauthorized invoke request")
    token = parts[1].strip()
    if token == "":
        raise HTTPException(status_code=401, detail="unauthorized invoke request")
    return token


def _verify_invoke_request(request: Request) -> InvokeScope:
    if not invoke_trust_config.enabled:
        return InvokeScope(agent_id=agent_id)

    token = _extract_bearer_token(request)
    try:
        claims: dict[str, Any]
        if jwks_client is not None:
            key = jwks_client.get_signing_key_from_jwt(token).key
            decoded = jwt.decode(
                token,
                key=key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                audience=invoke_trust_config.audience,
                issuer=invoke_trust_config.issuer,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
            claims = decoded if isinstance(decoded, dict) else {}
        elif shared_key_verifier is not None:
            claims = shared_key_verifier.verify(token, invoke_trust_config.signing_key)
        else:
            raise ValueError("invoke trust verifier is not configured")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=401, detail="unauthorized invoke request"
        ) from exc

    return InvokeScope(
        agent_id=str(claims.get("agent_id", "")).strip(),
        org_id=str(claims.get("org_id", "")).strip(),
        user_id=str(claims.get("user_id", "")).strip(),
        run_id=str(claims.get("run_id", "")).strip(),
    )


def _resolve_invoke_scope(
    req: InvokeRequest, scope: InvokeScope
) -> tuple[str, str, str, str]:
    scoped_agent_id = scope.agent_id if scope.agent_id != "" else agent_id
    scoped_run_id = scope.run_id if scope.run_id != "" else req.run_id
    scoped_org_id = scope.org_id if scope.org_id != "" else req.trace.tenant_id
    scoped_user_id = scope.user_id
    return scoped_agent_id, scoped_run_id, scoped_org_id, scoped_user_id


def _validate_scope_against_request(req: InvokeRequest, scope: InvokeScope) -> None:
    if scope.agent_id != "" and scope.agent_id != agent_id:
        raise HTTPException(status_code=401, detail="unauthorized invoke request")


def _load_workflow_client() -> Any:
    try:
        from simple_agents_py import Client
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail="simple_agents_py is not installed; install it before invoking workflows",
        ) from exc

    if simple_agents_api_key != "":
        os.environ.setdefault("CUSTOM_API_KEY", simple_agents_api_key)
    if simple_agents_api_base != "":
        os.environ.setdefault("CUSTOM_API_BASE", simple_agents_api_base)

    try:
        return Client(
            simple_agents_provider,
            api_base=simple_agents_api_base if simple_agents_api_base != "" else None,
            api_key=simple_agents_api_key if simple_agents_api_key != "" else None,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=(
                "simple_agents_py client failed to initialize; set CUSTOM_API_KEY "
                "or provider-specific credentials"
            ),
        ) from exc


def _build_workflow_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
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


def _workflow_text_output(terminal_output: Any) -> str:
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


def _extract_workflow_nerdstats(
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


def _count_workflow_events_by_type(workflow_result: dict[str, Any]) -> dict[str, int]:
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


def _build_trace_url(trace_id: str) -> str:
    normalized_trace_id = trace_id.strip()
    if normalized_trace_id == "":
        return ""
    base_url = os.getenv("TRACE_UI_BASE_URL", "http://localhost:16686").strip()
    normalized_base_url = base_url.rstrip("/")
    if normalized_base_url == "":
        normalized_base_url = "http://localhost:16686"
    return f"{normalized_base_url}/trace/{normalized_trace_id}"


def _build_chat_message_content(
    terminal_output: Any, workflow_result: dict[str, Any]
) -> dict[str, Any]:
    content: dict[str, Any] = {
        "reply": _workflow_text_output(terminal_output),
        "terminal_output": terminal_output,
        "workflow": {
            "workflow_id": workflow_result.get("workflow_id"),
            "terminal_node": workflow_result.get("terminal_node"),
        },
    }
    return content


def _build_chat_message_metadata(
    req: InvokeRequest, workflow_result: dict[str, Any]
) -> dict[str, Any]:
    trace_id = str(req.trace.trace_id).strip()
    metadata: dict[str, Any] = {
        "source": "runtime.workflow.invoke",
        "workflow_id": workflow_result.get("workflow_id"),
        "terminal_node": workflow_result.get("terminal_node"),
        "trace": workflow_result.get("trace", []),
        "step_timings": workflow_result.get("step_timings", []),
        "event_counts": _count_workflow_events_by_type(workflow_result),
        "nerdstats": _extract_workflow_nerdstats(workflow_result),
        "llm_node_metrics": workflow_result.get("llm_node_metrics", {}),
        "total_elapsed_ms": workflow_result.get("total_elapsed_ms"),
        "trace_context": {
            "trace_id": trace_id,
            "span_id": str(req.trace.span_id).strip(),
            "tenant_id": str(req.trace.tenant_id).strip(),
            "trace_url": _build_trace_url(trace_id),
        },
    }
    events = workflow_result.get("events")
    if isinstance(events, list):
        metadata["events"] = events
    return metadata


def _build_workflow_registry() -> dict[str, str]:
    subgraph_path = workflow_root / "hr-warning-email-subgraph.yaml"
    if not subgraph_path.exists():
        return {}

    resolved = str(subgraph_path)
    return {
        "hr_warning_email_subgraph": resolved,
        "hr-warning-email-subgraph": resolved,
    }


def _run_agent_workflow(req: InvokeRequest) -> dict[str, Any]:
    if not workflow_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"workflow file not found: {workflow_path}",
        )

    workflow_client = _load_workflow_client()
    workflow_input = {
        "messages": _build_workflow_messages(req.input),
        "email_text": str(req.input.get("message", "")).strip(),
        "workflow_registry": _build_workflow_registry(),
    }
    workflow_options = {
        "trace": {"tenant": {"run_id": req.run_id}},
        "telemetry": {"nerdstats": True},
    }
    try:
        return workflow_client.run_workflow_yaml(
            str(workflow_path),
            workflow_input,
            include_events=True,
            workflow_options=workflow_options,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"workflow execution failed: {exc}",
        ) from exc
    if scope.run_id != "" and scope.run_id != req.run_id:
        raise HTTPException(status_code=401, detail="unauthorized invoke request")


def _run_startup_bootstrap() -> None:
    if sdk_client is None:
        return
    if not env_bool("RUNTIME_BOOTSTRAP_REGISTER_ON_STARTUP", False):
        return

    registration = {
        "agent_id": agent_id,
        "agent_version": agent_version,
        "execution_mode": os.getenv(
            "RUNTIME_BOOTSTRAP_EXECUTION_MODE", "remote_runtime"
        ).strip()
        or "remote_runtime",
        "endpoint_url": os.getenv("RUNTIME_BOOTSTRAP_ENDPOINT_URL", "").strip(),
        "auth_mode": os.getenv("RUNTIME_BOOTSTRAP_AUTH_MODE", "jwt").strip() or "jwt",
        "capabilities": ["chat", "webhook", "queue"],
        "runtime_id": os.getenv("RUNTIME_BOOTSTRAP_RUNTIME_ID", "").strip(),
    }

    try:
        created = sdk_client.register_runtime(registration)
        logger.info(
            "runtime bootstrap register succeeded for %s@%s", agent_id, agent_version
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("runtime bootstrap register failed: %s", exc)
        return

    registration_id = _normalize_registration_id(created)
    if registration_id == "":
        registration_id = os.getenv("RUNTIME_BOOTSTRAP_REGISTRATION_ID", "").strip()
    if registration_id == "":
        logger.warning(
            "runtime bootstrap lifecycle skipped: registration_id unavailable"
        )
        return

    if env_bool("RUNTIME_BOOTSTRAP_VALIDATE_REGISTRATION", False):
        try:
            sdk_client.validate_runtime_registration(registration_id)
            logger.info("runtime bootstrap validate succeeded")
        except Exception as exc:  # noqa: BLE001
            logger.warning("runtime bootstrap validate failed: %s", exc)

    if env_bool("RUNTIME_BOOTSTRAP_ACTIVATE_REGISTRATION", False):
        try:
            sdk_client.activate_runtime_registration(registration_id)
            logger.info("runtime bootstrap activate succeeded")
        except Exception as exc:  # noqa: BLE001
            logger.warning("runtime bootstrap activate failed: %s", exc)


def _read_auth_bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "").strip()
    if authorization == "":
        return ""

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].strip().lower() != "bearer":
        raise HTTPException(status_code=401, detail="invalid authorization header")

    token = parts[1].strip()
    if token == "":
        raise HTTPException(status_code=401, detail="invalid authorization header")
    return token


def _build_control_plane_client(
    request: Request,
    *,
    require_bearer: bool = False,
    allow_api_token_fallback: bool = True,
) -> SimpleFlowClient:
    if api_base_url == "":
        raise HTTPException(
            status_code=503,
            detail="control plane is not configured; set SIMPLEFLOW_API_BASE_URL",
        )

    bearer_token = _read_auth_bearer_token(request)
    if require_bearer and bearer_token == "":
        raise HTTPException(status_code=401, detail="authorization required")

    selected_token = bearer_token
    if selected_token == "" and allow_api_token_fallback:
        selected_token = api_token
    return SimpleFlowClient(api_base_url, selected_token)


def _control_plane_query_path(path: str, query: dict[str, Any]) -> str:
    encoded = urlencode(query)
    if encoded == "":
        return path
    return f"{path}?{encoded}"


def _map_control_plane_error(exc: Exception) -> HTTPException:
    raw = str(exc).strip()
    status_code = 502
    detail = raw if raw != "" else "control-plane request failed"

    if "status=" in raw:
        try:
            status_fragment = raw.split("status=", 1)[1].split(" ", 1)[0]
            status_code = int(status_fragment)
        except Exception:  # noqa: BLE001
            status_code = 502

    if "body=" in raw:
        body = raw.split("body=", 1)[1].strip()
        if body != "":
            detail = body

    return HTTPException(status_code=status_code, detail=detail)


def _control_plane_get(
    request: Request,
    *,
    path: str,
    require_bearer: bool = False,
) -> dict[str, Any]:
    client = _build_control_plane_client(request, require_bearer=require_bearer)
    try:
        return client._get(path)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _map_control_plane_error(exc) from exc
    finally:
        client.close()


def _control_plane_post(
    request: Request,
    *,
    path: str,
    payload: dict[str, Any],
    require_bearer: bool = False,
    allow_api_token_fallback: bool = True,
) -> dict[str, Any]:
    client = _build_control_plane_client(
        request,
        require_bearer=require_bearer,
        allow_api_token_fallback=allow_api_token_fallback,
    )
    try:
        return client._post(path, payload)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _map_control_plane_error(exc) from exc
    finally:
        client.close()


def _control_plane_patch(
    request: Request,
    *,
    path: str,
    payload: dict[str, Any],
    require_bearer: bool = False,
) -> dict[str, Any]:
    client = _build_control_plane_client(request, require_bearer=require_bearer)
    try:
        return client._patch(path, payload)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _map_control_plane_error(exc) from exc
    finally:
        client.close()


def _control_plane_delete(
    request: Request,
    *,
    path: str,
    require_bearer: bool = False,
) -> dict[str, Any]:
    client = _build_control_plane_client(request, require_bearer=require_bearer)
    try:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{api_base_url.rstrip('/')}{normalized_path}"
        headers: dict[str, str] = {}
        token = _read_auth_bearer_token(request)
        if token != "":
            headers["Authorization"] = f"Bearer {token}"
        elif api_token != "":
            headers["Authorization"] = f"Bearer {api_token}"

        response = client._client.delete(url, headers=headers)
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"simpleflow sdk request error: status={response.status_code} body={response.text.strip()}"
            )
        if response.text.strip() == "":
            return {}
        decoded = response.json()
        if isinstance(decoded, dict):
            return decoded
        return {}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _map_control_plane_error(exc) from exc
    finally:
        client.close()


def _require_operator_auth(request: Request) -> None:
    token = _read_auth_bearer_token(request)
    if token == "":
        raise HTTPException(status_code=401, detail="authorization required")


def _find_agent_config(
    runtime_agent_id: str, runtime_agent_version: str
) -> dict[str, Any] | None:
    key = _agent_key(runtime_agent_id, runtime_agent_version)
    for item in agent_catalog:
        if _agent_key(item.get("agent_id", ""), item.get("agent_version", "")) == key:
            return item
    return None


def _require_known_agent(runtime_agent_id: str, runtime_agent_version: str) -> None:
    if _find_agent_config(runtime_agent_id, runtime_agent_version) is not None:
        return
    raise HTTPException(status_code=404, detail="agent is not available in catalog")


def _require_known_agent_id(runtime_agent_id: str) -> None:
    trimmed = runtime_agent_id.strip()
    for item in agent_catalog:
        if str(item.get("agent_id", "")).strip() == trimmed:
            return
    raise HTTPException(status_code=404, detail="agent is not available in catalog")


def _read_non_empty_string(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _read_string_field(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key in payload:
            value = _read_non_empty_string(payload.get(key))
            if value != "":
                return value
    return ""


def _normalize_registration_id(payload: dict[str, Any]) -> str:
    return _read_string_field(
        payload,
        (
            "registration_id",
            "registrationId",
            "RegistrationID",
            "id",
            "ID",
        ),
    )


def _normalize_registration_status(payload: dict[str, Any]) -> str:
    raw_status = _read_string_field(
        payload,
        (
            "status",
            "Status",
            "registration_status",
            "registrationStatus",
            "RegistrationStatus",
        ),
    ).lower()
    if raw_status in {"active", "activated", "ready"}:
        return "active"
    if raw_status in {
        "draft",
        "pending",
        "created",
        "registered",
        "validated",
        "validating",
        "activating",
        "in_progress",
        "in-progress",
    }:
        return "in_progress"
    return "unknown"


def _normalize_runtime_registration(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "registration_id": _normalize_registration_id(item),
        "status": _normalize_registration_status(item),
        "raw": item,
    }


def _normalize_runtime_registrations_payload(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = (
        payload.get("registrations"),
        payload.get("Registrations"),
        payload.get("items"),
        payload.get("Items"),
    )
    raw_items: list[Any] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            raw_items = candidate
            break

    normalized: list[dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, dict):
            normalized.append(_normalize_runtime_registration(item))
    return normalized


def _normalize_control_plane_me(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    user_payload = payload.get("user")
    user = user_payload if isinstance(user_payload, dict) else {}

    user_id = ""
    for candidate in (
        payload.get("id"),
        payload.get("user_id"),
        payload.get("userId"),
        payload.get("UserID"),
        user.get("id"),
        user.get("user_id"),
        user.get("userId"),
        user.get("UserID"),
    ):
        user_id = _read_non_empty_string(candidate)
        if user_id != "":
            break

    organization_id = ""
    for candidate in (
        payload.get("organization_id"),
        payload.get("organizationId"),
        payload.get("OrganizationID"),
        user.get("organization_id"),
        user.get("organizationId"),
        user.get("OrganizationID"),
    ):
        organization_id = _read_non_empty_string(candidate)
        if organization_id != "":
            break

    if user_id != "":
        normalized["id"] = user_id
        normalized["user_id"] = user_id
    if organization_id != "":
        normalized["organization_id"] = organization_id
    return normalized


def _onboarding_state_from_catalog(item: dict[str, Any]) -> dict[str, Any]:
    state = {
        "onboarding_id": f"onb_{uuid4().hex}",
        "agent_id": str(item.get("agent_id", "")).strip(),
        "agent_version": str(item.get("agent_version", "")).strip(),
        "runtime_id": str(item.get("runtime_id", "")).strip(),
        "endpoint_url": str(item.get("endpoint_url", "")).strip(),
        "state": "not_started",
        "message": "Onboarding has not started.",
        "registration_id": "",
        "steps": {
            "create": "pending",
            "validate": "pending",
            "activate": "pending",
        },
    }
    return state


def _get_or_create_onboarding_state(
    runtime_agent_id: str, runtime_agent_version: str
) -> dict[str, Any]:
    key = _agent_key(runtime_agent_id, runtime_agent_version)
    existing = onboarding_states.get(key)
    if existing is not None:
        return existing

    config = _find_agent_config(runtime_agent_id, runtime_agent_version)
    if config is None:
        raise HTTPException(status_code=404, detail="agent is not available in catalog")
    state = _onboarding_state_from_catalog(config)
    onboarding_states[key] = state
    return state


def _build_machine_control_plane_client() -> SimpleFlowClient:
    if api_base_url == "":
        raise HTTPException(
            status_code=503,
            detail="control plane is not configured; set SIMPLEFLOW_API_BASE_URL",
        )
    if machine_client_id != "" and machine_client_secret != "":
        return SimpleFlowClient(
            api_base_url,
            oauth_client_id=machine_client_id,
            oauth_client_secret=machine_client_secret,
        )
    if api_token == "":
        raise HTTPException(
            status_code=503,
            detail="machine credentials are required; set SIMPLEFLOW_CLIENT_ID and SIMPLEFLOW_CLIENT_SECRET",
        )
    return SimpleFlowClient(api_base_url, api_token)


def _run_onboarding_lifecycle(
    runtime_agent_id: str,
    runtime_agent_version: str,
) -> dict[str, Any]:
    _require_known_agent(runtime_agent_id, runtime_agent_version)
    state = _get_or_create_onboarding_state(runtime_agent_id, runtime_agent_version)
    state["state"] = "in_progress"
    state["message"] = "Running runtime registration lifecycle."
    state["steps"] = {
        "create": "running",
        "validate": "pending",
        "activate": "pending",
    }

    config = _find_agent_config(runtime_agent_id, runtime_agent_version)
    if config is None:
        raise HTTPException(status_code=404, detail="agent is not available in catalog")

    registration = {
        "agent_id": runtime_agent_id,
        "agent_version": runtime_agent_version,
        "execution_mode": "remote_runtime",
        "endpoint_url": str(config.get("endpoint_url", "")).strip(),
        "auth_mode": "jwt",
        "capabilities": ["chat", "webhook", "queue"],
        "runtime_id": str(config.get("runtime_id", "")).strip(),
    }

    client = _build_machine_control_plane_client()
    try:
        created = client.register_runtime(registration)
        registration_id = _normalize_registration_id(created)
        if registration_id == "":
            state["state"] = "failed"
            state["steps"]["create"] = "failed"
            raise HTTPException(
                status_code=502,
                detail="registration response missing registration id",
            )

        state["registration_id"] = registration_id
        state["steps"]["create"] = "success"
        state["steps"]["validate"] = "running"

        validation = client.validate_runtime_registration(registration_id)
        if isinstance(validation, dict) and validation.get("validation_ok") is False:
            raise HTTPException(
                status_code=502, detail="runtime registration validation failed"
            )

        state["steps"]["validate"] = "success"
        state["steps"]["activate"] = "running"
        client.activate_runtime_registration(registration_id)
        result = {
            "registration_id": registration_id,
        }
    except SimpleFlowAuthenticationError as exc:
        state["state"] = "blocked"
        state["message"] = "machine auth failed for onboarding lifecycle"
        state["steps"]["create"] = "failed"
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except SimpleFlowAuthorizationError as exc:
        state["state"] = "blocked"
        state["message"] = "machine token lacks lifecycle scope"
        state["steps"]["create"] = "failed"
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SimpleFlowLifecycleError as exc:
        state["state"] = "failed"
        state["message"] = str(exc)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception as exc:  # noqa: BLE001
        state["state"] = "failed"
        state["message"] = str(exc)
        raise _map_control_plane_error(exc) from exc
    finally:
        client.close()

    state["registration_id"] = str(result.get("registration_id", "")).strip()
    state["steps"]["create"] = "success"
    state["steps"]["validate"] = "success"
    state["steps"]["activate"] = "success"
    state["state"] = "active"
    state["message"] = "Runtime registration is active."
    return state


def _onboarding_public_view(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "onboarding_id": state.get("onboarding_id", ""),
        "agent_id": state.get("agent_id", ""),
        "agent_version": state.get("agent_version", ""),
        "state": state.get("state", "not_started"),
        "message": state.get("message", ""),
        "registration_id": state.get("registration_id", ""),
        "steps": state.get("steps", {}),
    }


def _sync_onboarding_state_from_control_plane(
    state: dict[str, Any], request: Request
) -> dict[str, Any]:
    runtime_agent_id = str(state.get("agent_id", "")).strip()
    runtime_agent_version = str(state.get("agent_version", "")).strip()
    if runtime_agent_id == "" or runtime_agent_version == "":
        return state

    path = _control_plane_query_path(
        "/v1/runtime/registrations",
        {
            "agent_id": runtime_agent_id,
            "agent_version": runtime_agent_version,
        },
    )

    try:
        payload = _control_plane_get(request, path=path, require_bearer=True)
    except HTTPException:
        return state

    registrations = _normalize_runtime_registrations_payload(payload)
    if len(registrations) == 0:
        state["registration_id"] = ""
        state["state"] = "not_started"
        state["message"] = "Onboarding has not started."
        state["steps"] = {
            "create": "pending",
            "validate": "pending",
            "activate": "pending",
        }
        return state

    active_registration = next(
        (item for item in registrations if item.get("status") == "active"),
        None,
    )
    selected = (
        active_registration if active_registration is not None else registrations[0]
    )
    registration_id = _read_non_empty_string(selected.get("registration_id"))
    state["registration_id"] = registration_id

    if active_registration is not None:
        state["state"] = "active"
        state["message"] = "Onboarding is complete. Chat is enabled."
        state["steps"] = {
            "create": "success",
            "validate": "success",
            "activate": "success",
        }
        return state

    state["state"] = "in_progress"
    state["message"] = "Runtime registration exists but is not active yet."
    state["steps"] = {
        "create": "success",
        "validate": "pending",
        "activate": "pending",
    }
    return state


@app.on_event("startup")
def on_startup() -> None:
    _run_startup_bootstrap()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "python-remote-runtime"}


@app.get("/meta")
def meta() -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "agent_version": agent_version,
        "capabilities": ["chat", "webhook", "queue"],
        "paths": {
            "invoke": "/invoke",
            "health": "/health",
            "meta": "/meta",
            "webhook": "/webhook",
            "queue": "/queue/enqueue",
        },
    }


@app.get("/api/agents/available")
def available_agents(request: Request) -> dict[str, Any]:
    _require_operator_auth(request)
    agents: list[dict[str, Any]] = []
    for item in agent_catalog:
        state = _get_or_create_onboarding_state(
            str(item.get("agent_id", "")).strip(),
            str(item.get("agent_version", "")).strip(),
        )
        state = _sync_onboarding_state_from_control_plane(state, request)
        agents.append(
            {
                "agent_id": item.get("agent_id", ""),
                "agent_version": item.get("agent_version", ""),
                "runtime_id": item.get("runtime_id", ""),
                "endpoint_url": item.get("endpoint_url", ""),
                "onboarding": _onboarding_public_view(state),
            }
        )
    return {
        "agents": agents,
        "default_agent": {
            "agent_id": agent_id,
            "agent_version": agent_version,
        },
    }


@app.post("/api/onboarding/start")
def onboarding_start(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_operator_auth(request)
    runtime_agent_id = str(payload.get("agent_id", agent_id)).strip()
    runtime_agent_version = str(payload.get("agent_version", agent_version)).strip()
    state = _run_onboarding_lifecycle(runtime_agent_id, runtime_agent_version)
    return _onboarding_public_view(state)


@app.get("/api/onboarding/status")
def onboarding_status(
    request: Request,
    onboarding_id: str = "",
    agent_id: str = "",
    agent_version: str = "",
) -> dict[str, Any]:
    _require_operator_auth(request)
    if onboarding_id.strip() != "":
        for state in onboarding_states.values():
            if str(state.get("onboarding_id", "")).strip() == onboarding_id.strip():
                return _onboarding_public_view(state)
        raise HTTPException(status_code=404, detail="onboarding record not found")

    runtime_agent_id = (
        agent_id.strip()
        or os.getenv("RUNTIME_AGENT_ID", "sample-python-runtime").strip()
    )
    runtime_agent_version = (
        agent_version.strip() or os.getenv("RUNTIME_AGENT_VERSION", "v1").strip()
    )
    state = _get_or_create_onboarding_state(runtime_agent_id, runtime_agent_version)
    state = _sync_onboarding_state_from_control_plane(state, request)
    if str(state.get("state", "")).strip().lower() == "not_started":
        try:
            state = _run_onboarding_lifecycle(runtime_agent_id, runtime_agent_version)
        except HTTPException as exc:
            if exc.status_code == 401:
                state["state"] = "blocked"
                state["message"] = "machine auth failed for onboarding lifecycle"
            elif exc.status_code == 403:
                state["state"] = "blocked"
                state["message"] = "machine token lacks lifecycle scope"
            else:
                state["state"] = "failed"
                detail = str(exc.detail).strip()
                state["message"] = detail if detail != "" else "onboarding failed"
    return _onboarding_public_view(state)


@app.post("/api/onboarding/retry")
def onboarding_retry(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_operator_auth(request)
    runtime_agent_id = str(payload.get("agent_id", agent_id)).strip()
    runtime_agent_version = str(payload.get("agent_version", agent_version)).strip()
    state = _run_onboarding_lifecycle(runtime_agent_id, runtime_agent_version)
    return _onboarding_public_view(state)


@app.post("/api/control-plane/auth/sessions")
def control_plane_create_auth_session(
    payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    return _control_plane_post(
        request,
        path="/v1/auth/sessions",
        payload=payload,
        allow_api_token_fallback=False,
    )


@app.delete("/api/control-plane/auth/sessions/current")
def control_plane_delete_auth_session(request: Request) -> dict[str, Any]:
    return _control_plane_delete(
        request,
        path="/v1/auth/sessions/current",
        require_bearer=True,
    )


@app.get("/api/control-plane/me")
def control_plane_me(request: Request) -> dict[str, Any]:
    payload = _control_plane_get(request, path="/v1/me", require_bearer=True)
    return _normalize_control_plane_me(payload)


@app.get("/api/control-plane/runtime/registrations")
def control_plane_runtime_registrations(
    request: Request,
    agent_id: str,
    agent_version: str,
) -> dict[str, Any]:
    _require_known_agent(agent_id, agent_version)
    path = _control_plane_query_path(
        "/v1/runtime/registrations",
        {
            "agent_id": agent_id,
            "agent_version": agent_version,
        },
    )
    return _control_plane_get(request, path=path, require_bearer=True)


@app.post("/api/control-plane/runtime/invoke")
def control_plane_runtime_invoke(
    payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    invoke_agent_id = str(payload.get("agent_id", "")).strip()
    invoke_agent_version = str(payload.get("agent_version", "")).strip()
    _require_known_agent(invoke_agent_id, invoke_agent_version)
    return _control_plane_post(
        request,
        path="/v1/runtime/invoke",
        payload=payload,
        require_bearer=True,
    )


@app.get("/api/control-plane/chat/history/sessions")
def control_plane_chat_history_sessions(
    request: Request,
    agent_id: str,
    user_id: str,
    status: str = "active",
    limit: int = 50,
) -> dict[str, Any]:
    _require_known_agent_id(agent_id)
    path = _control_plane_query_path(
        "/v1/chat/history/sessions",
        {
            "agent_id": agent_id,
            "user_id": user_id,
            "status": status,
            "limit": limit,
        },
    )
    return _control_plane_get(request, path=path, require_bearer=True)


@app.get("/api/control-plane/chat/history/messages")
def control_plane_chat_history_messages(
    request: Request,
    agent_id: str,
    chat_id: str,
    user_id: str,
    limit: int = 200,
) -> dict[str, Any]:
    _require_known_agent_id(agent_id)
    path = _control_plane_query_path(
        "/v1/chat/history/messages",
        {
            "agent_id": agent_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "limit": limit,
        },
    )
    return _control_plane_get(request, path=path, require_bearer=True)


@app.post("/api/control-plane/chat/history/messages")
def control_plane_create_chat_history_message(
    payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    payload_agent_id = str(payload.get("agent_id", "")).strip()
    _require_known_agent_id(payload_agent_id)
    return _control_plane_post(
        request,
        path="/v1/chat/history/messages",
        payload=payload,
        require_bearer=True,
    )


@app.patch("/api/control-plane/chat/history/messages/{message_id}")
def control_plane_patch_chat_history_message(
    message_id: str,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    payload_agent_id = str(payload.get("agent_id", "")).strip()
    _require_known_agent_id(payload_agent_id)
    return _control_plane_patch(
        request,
        path=f"/v1/chat/history/messages/{message_id}",
        payload=payload,
        require_bearer=True,
    )


@app.post("/invoke")
def invoke(req: InvokeRequest, request: Request) -> dict[str, Any]:
    scope = _verify_invoke_request(request)
    _validate_scope_against_request(req, scope)

    if req.mode not in {"realtime", "batch"}:
        raise HTTPException(status_code=400, detail="mode must be realtime or batch")

    scoped_agent_id, scoped_run_id, scoped_org_id, scoped_user_id = (
        _resolve_invoke_scope(req, scope)
    )

    now_ms = int(time.time() * 1000)

    workflow_result = _run_agent_workflow(req)
    terminal_output = _workflow_text_output(workflow_result.get("terminal_output"))
    chat_message_content = _build_chat_message_content(
        workflow_result.get("terminal_output"), workflow_result
    )
    chat_message_metadata = _build_chat_message_metadata(req, workflow_result)
    trace_context = chat_message_metadata.get("trace_context")
    trace_url = ""
    if isinstance(trace_context, dict):
        trace_url = str(trace_context.get("trace_url", "")).strip()

    if sdk_client is not None:
        try:
            sdk_client.report_runtime_event(
                RuntimeEvent(
                    type="runtime.invoke.completed",
                    agent_id=scoped_agent_id,
                    agent_version=agent_version,
                    run_id=scoped_run_id,
                    organization_id=scoped_org_id,
                    user_id=scoped_user_id if scoped_user_id != "" else None,
                    timestamp_ms=now_ms,
                    payload={
                        "status": "ok",
                        "workflow_id": workflow_result.get("workflow_id"),
                        "terminal_node": workflow_result.get("terminal_node"),
                        "trace_id": req.trace.trace_id,
                        "trace_url": trace_url,
                        "event_counts": chat_message_metadata.get("event_counts", {}),
                        "nerdstats": chat_message_metadata.get("nerdstats"),
                    },
                )
            )
            sdk_client.write_chat_message(
                ChatMessageWrite(
                    agent_id=scoped_agent_id,
                    organization_id=scoped_org_id,
                    run_id=scoped_run_id,
                    role="assistant",
                    content=chat_message_content,
                    metadata=chat_message_metadata,
                    created_at_ms=now_ms,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("runtime SDK write skipped: %s", exc)

    return {
        "schema_version": "v1",
        "run_id": scoped_run_id,
        "status": "ok",
        "output": {
            "reply": terminal_output,
            "workflow_id": workflow_result.get("workflow_id"),
            "terminal_node": workflow_result.get("terminal_node"),
            "trace_url": trace_url,
        },
        "error": None,
        "metrics": {
            "started_at_ms": now_ms,
            "finished_at_ms": int(time.time() * 1000),
            "duration_ms": 1,
        },
    }


@app.post("/webhook")
def webhook(payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "accepted", "source": "webhook", "keys": sorted(payload.keys())}


@app.post("/queue/enqueue")
def queue_enqueue(message: QueueMessage) -> dict[str, Any]:
    queue_buffer.append(message)

    if sdk_client is not None:
        sdk_client.publish_queue_contract(
            QueueContract(
                queue_name="default",
                message_id=message.message_id,
                idempotency_key=message.message_id,
                retry_attempt=0,
                max_retry_attempt=3,
                payload=message.payload,
            )
        )

    return {"status": "queued", "size": len(queue_buffer)}


@app.post("/queue/process")
def queue_process() -> dict[str, int]:
    processed = len(queue_buffer)
    queue_buffer.clear()
    return {"processed": processed}
