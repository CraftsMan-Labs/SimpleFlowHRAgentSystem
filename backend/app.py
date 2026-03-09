from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from simpleflow_sdk import (
    ChatMessageWrite,
    InvokeTokenVerifier,
    QueueContract,
    RuntimeEvent,
    RuntimeRegistration,
    SimpleFlowClient,
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
sdk_client = SimpleFlowClient(api_base_url, api_token) if api_base_url else None

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
    if signing_key != "" and jwks_url != "":
        raise ValueError(
            "set only one of RUNTIME_INVOKE_TOKEN_SIGNING_KEY or RUNTIME_INVOKE_TOKEN_JWKS_URL"
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
        if shared_key_verifier is not None:
            claims = shared_key_verifier.verify(token, invoke_trust_config.signing_key)
        elif jwks_client is not None:
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

    registration = RuntimeRegistration(
        agent_id=agent_id,
        agent_version=agent_version,
        execution_mode=os.getenv(
            "RUNTIME_BOOTSTRAP_EXECUTION_MODE", "remote_runtime"
        ).strip()
        or "remote_runtime",
        endpoint_url=os.getenv("RUNTIME_BOOTSTRAP_ENDPOINT_URL", "").strip() or None,
        auth_mode=os.getenv("RUNTIME_BOOTSTRAP_AUTH_MODE", "jwt").strip() or "jwt",
        capabilities=["chat", "webhook", "queue"],
        runtime_id=os.getenv("RUNTIME_BOOTSTRAP_RUNTIME_ID", "").strip() or None,
    )

    try:
        created = sdk_client.register_runtime(registration)
        logger.info(
            "runtime bootstrap register succeeded for %s@%s", agent_id, agent_version
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("runtime bootstrap register failed: %s", exc)
        return

    registration_id = str(created.get("id", "")).strip()
    if registration_id == "":
        registration_id = str(created.get("registration_id", "")).strip()
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
                    },
                )
            )
            sdk_client.write_chat_message(
                ChatMessageWrite(
                    agent_id=scoped_agent_id,
                    organization_id=scoped_org_id,
                    run_id=scoped_run_id,
                    role="assistant",
                    content=terminal_output,
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
