from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from simpleflow_sdk import (
    ChatMessageWrite,
    InvokeTokenVerifier,
    QueueContract,
    SimpleFlowAuthenticationError,
    SimpleFlowAuthorizationError,
    SimpleFlowClient,
    SimpleFlowLifecycleError,
)
from config import get_settings
from runtime_helpers.control_plane import (
    ControlPlaneConfig,
)
from runtime_helpers.workflow import (
    build_chat_message_content,
    build_chat_message_metadata,
    build_workflow_messages,
    extract_workflow_nerdstats,
    resolve_chat_id,
    token_metrics_from_nerdstats,
    workflow_text_output,
)
from services.control_plane_service import ControlPlaneService
from services.onboarding_service import OnboardingService
from services.runtime_service import RuntimeService


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
settings = get_settings()
cors_allow_origins = settings.cors_allow_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

queue_buffer: deque[QueueMessage] = deque()

agent_id = settings.runtime_agent_id
agent_version = settings.runtime_agent_version

api_base_url = settings.simpleflow_api_base_url
api_token = settings.simpleflow_api_token
machine_client_id = settings.simpleflow_client_id
machine_client_secret = settings.simpleflow_client_secret
control_plane_config = ControlPlaneConfig(
    api_base_url=api_base_url, api_token=api_token
)
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

control_plane_service = ControlPlaneService(control_plane_config)
onboarding_service = OnboardingService(
    settings=settings,
    control_plane=control_plane_service,
    logger=logger,
)
runtime_service = RuntimeService(settings)


def _agent_key(runtime_agent_id: str, runtime_agent_version: str) -> str:
    return f"{runtime_agent_id.strip()}::{runtime_agent_version.strip()}"


def _build_agent_catalog() -> list[dict[str, Any]]:
    return onboarding_service.agent_catalog


agent_catalog = _build_agent_catalog()
onboarding_states = onboarding_service.onboarding_states


def _build_invoke_trust_config() -> InvokeTrustConfig:
    get_settings.cache_clear()
    current_settings = get_settings()
    enabled = current_settings.runtime_invoke_trust_enabled
    issuer = current_settings.runtime_invoke_token_issuer
    audience = current_settings.runtime_invoke_token_audience
    signing_key = current_settings.runtime_invoke_token_signing_key
    jwks_url = current_settings.runtime_invoke_token_jwks_url

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
    return runtime_service.load_workflow_client()


def _build_workflow_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return build_workflow_messages(payload)


def _workflow_text_output(terminal_output: Any) -> str:
    return workflow_text_output(terminal_output)


def _extract_workflow_nerdstats(
    workflow_result: dict[str, Any],
) -> dict[str, Any] | None:
    return extract_workflow_nerdstats(workflow_result)


def _build_chat_message_content(
    terminal_output: Any, workflow_result: dict[str, Any]
) -> dict[str, Any]:
    return build_chat_message_content(terminal_output, workflow_result)


def _build_chat_message_metadata(
    req: InvokeRequest, workflow_result: dict[str, Any]
) -> dict[str, Any]:
    return build_chat_message_metadata(req, workflow_result)


def _token_metrics_from_nerdstats(nerdstats: Any) -> dict[str, int]:
    return token_metrics_from_nerdstats(nerdstats)


def _resolve_chat_id(input_payload: dict[str, Any], fallback_run_id: str) -> str:
    return resolve_chat_id(input_payload, fallback_run_id)


def _build_workflow_registry() -> dict[str, str]:
    return runtime_service.build_workflow_registry()


def _run_agent_workflow(req: InvokeRequest) -> dict[str, Any]:
    return runtime_service.run_agent_workflow(req)


def _run_startup_bootstrap() -> None:
    if sdk_client is None:
        return
    if not settings.runtime_bootstrap_register_on_startup:
        return

    registration = {
        "agent_id": agent_id,
        "agent_version": agent_version,
        "execution_mode": settings.runtime_bootstrap_execution_mode,
        "endpoint_url": settings.runtime_bootstrap_endpoint_url,
        "auth_mode": settings.runtime_bootstrap_auth_mode,
        "capabilities": ["chat", "webhook", "queue"],
        "runtime_id": settings.runtime_bootstrap_runtime_id,
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
        registration_id = settings.runtime_bootstrap_registration_id
    if registration_id == "":
        logger.warning(
            "runtime bootstrap lifecycle skipped: registration_id unavailable"
        )
        return

    if settings.runtime_bootstrap_validate_registration:
        try:
            sdk_client.validate_runtime_registration(registration_id)
            logger.info("runtime bootstrap validate succeeded")
        except Exception as exc:  # noqa: BLE001
            logger.warning("runtime bootstrap validate failed: %s", exc)

    if settings.runtime_bootstrap_activate_registration:
        try:
            sdk_client.activate_runtime_registration(registration_id)
            logger.info("runtime bootstrap activate succeeded")
        except Exception as exc:  # noqa: BLE001
            logger.warning("runtime bootstrap activate failed: %s", exc)


def _read_auth_bearer_token(request: Request) -> str:
    return control_plane_service.read_auth_bearer_token(request)


def _build_control_plane_client(
    request: Request,
    *,
    require_bearer: bool = False,
    allow_api_token_fallback: bool = True,
) -> SimpleFlowClient:
    return control_plane_service.build_client(
        request,
        require_bearer=require_bearer,
        allow_api_token_fallback=allow_api_token_fallback,
    )


def _control_plane_query_path(path: str, query: dict[str, Any]) -> str:
    return control_plane_service.query_path(path, query)


def _map_control_plane_error(exc: Exception) -> HTTPException:
    return control_plane_service.map_error(exc)


def _control_plane_get(
    request: Request,
    *,
    path: str,
    require_bearer: bool = False,
) -> dict[str, Any]:
    return control_plane_service.get(
        request,
        path=path,
        require_bearer=require_bearer,
    )


def _control_plane_post(
    request: Request,
    *,
    path: str,
    payload: dict[str, Any],
    require_bearer: bool = False,
    allow_api_token_fallback: bool = True,
) -> dict[str, Any]:
    return control_plane_service.post(
        request,
        path=path,
        payload=payload,
        require_bearer=require_bearer,
        allow_api_token_fallback=allow_api_token_fallback,
    )


def _control_plane_patch(
    request: Request,
    *,
    path: str,
    payload: dict[str, Any],
    require_bearer: bool = False,
) -> dict[str, Any]:
    return control_plane_service.patch(
        request,
        path=path,
        payload=payload,
        require_bearer=require_bearer,
    )


def _control_plane_delete(
    request: Request,
    *,
    path: str,
    require_bearer: bool = False,
) -> dict[str, Any]:
    return control_plane_service.delete(
        request,
        path=path,
        require_bearer=require_bearer,
    )


def _require_operator_auth(request: Request) -> None:
    token = _read_auth_bearer_token(request)
    if token == "":
        raise HTTPException(status_code=401, detail="authorization required")


def _find_agent_config(
    runtime_agent_id: str, runtime_agent_version: str
) -> dict[str, Any] | None:
    return onboarding_service.find_agent_config(runtime_agent_id, runtime_agent_version)


def _require_known_agent(runtime_agent_id: str, runtime_agent_version: str) -> None:
    onboarding_service.require_known_agent(runtime_agent_id, runtime_agent_version)


def _require_known_agent_id(runtime_agent_id: str) -> None:
    onboarding_service.require_known_agent_id(runtime_agent_id)


def _read_non_empty_string(value: Any) -> str:
    return onboarding_service._read_non_empty_string(value)


def _read_string_field(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    return onboarding_service._read_string_field(payload, keys)


def _normalize_registration_id(payload: dict[str, Any]) -> str:
    return onboarding_service.normalize_registration_id(payload)


def _normalize_registration_status(payload: dict[str, Any]) -> str:
    return onboarding_service._normalize_registration_status(payload)


def _normalize_runtime_registration(item: dict[str, Any]) -> dict[str, Any]:
    return onboarding_service._normalize_runtime_registration(item)


def _normalize_runtime_registrations_payload(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    return onboarding_service._normalize_runtime_registrations_payload(payload)


def _normalize_control_plane_me(payload: dict[str, Any]) -> dict[str, Any]:
    return onboarding_service.normalize_control_plane_me(payload)


def _onboarding_state_from_catalog(item: dict[str, Any]) -> dict[str, Any]:
    return onboarding_service.onboarding_state_from_catalog(item)


def _get_or_create_onboarding_state(
    runtime_agent_id: str, runtime_agent_version: str
) -> dict[str, Any]:
    return onboarding_service.get_or_create_onboarding_state(
        runtime_agent_id,
        runtime_agent_version,
    )


def _build_machine_control_plane_client() -> SimpleFlowClient:
    return onboarding_service._build_machine_control_plane_client()


def _run_onboarding_lifecycle(
    runtime_agent_id: str,
    runtime_agent_version: str,
) -> dict[str, Any]:
    _require_known_agent(runtime_agent_id, runtime_agent_version)
    state = _get_or_create_onboarding_state(runtime_agent_id, runtime_agent_version)
    state["state"] = "in_progress"
    state["message"] = "Running runtime registration lifecycle."
    state["steps"] = {
        "create": "pending",
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
        ensure_registration_active = getattr(
            client, "ensure_runtime_registration_active", None
        )
        result: dict[str, Any]
        if callable(ensure_registration_active):
            ensured = ensure_registration_active(registration=registration)
            if not isinstance(ensured, dict):
                raise HTTPException(
                    status_code=502,
                    detail="registration lifecycle response must be an object",
                )
            result = ensured
        else:
            state["steps"]["create"] = "running"
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
            if (
                isinstance(validation, dict)
                and validation.get("validation_ok") is False
            ):
                raise HTTPException(
                    status_code=502,
                    detail="runtime registration validation failed",
                )

            state["steps"]["validate"] = "success"
            state["steps"]["activate"] = "running"
            client.activate_runtime_registration(registration_id)
            result = {
                "registration_id": registration_id,
                "created": True,
                "validated": True,
                "activated": True,
            }

        registration_id = _normalize_registration_id(result)
        if registration_id == "":
            state["state"] = "failed"
            state["steps"]["create"] = "failed"
            raise HTTPException(
                status_code=502,
                detail="registration response missing registration id",
            )

        state["registration_id"] = registration_id
        if result.get("created") is True:
            state["steps"]["create"] = "success"
        else:
            state["steps"]["create"] = "skipped"
        if result.get("validated") is True:
            state["steps"]["validate"] = "success"
        else:
            state["steps"]["validate"] = "skipped"
        if result.get("activated") is True:
            state["steps"]["activate"] = "success"
        else:
            state["steps"]["activate"] = "skipped"
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
    state["state"] = "active"
    state["message"] = "Runtime registration is active."
    return state


def _onboarding_public_view(state: dict[str, Any]) -> dict[str, Any]:
    return onboarding_service.onboarding_public_view(state)


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

    runtime_agent_id = agent_id.strip() or settings.runtime_agent_id
    runtime_agent_version = agent_version.strip() or settings.runtime_agent_version
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
    nerdstats_payload = chat_message_metadata.get("nerdstats")
    token_metrics = _token_metrics_from_nerdstats(nerdstats_payload)
    chat_id = _resolve_chat_id(req.input, scoped_run_id)
    message_id = f"assistant-{uuid4().hex}"
    event_idempotency_key = f"runtime-event-{scoped_run_id}"
    chat_idempotency_key = f"runtime-chat-{message_id}"
    trace_context = chat_message_metadata.get("trace_context")
    trace_url = ""
    if isinstance(trace_context, dict):
        trace_url = str(trace_context.get("trace_url", "")).strip()

    runtime_write_client: SimpleFlowClient | None = None
    if sdk_client is not None:
        try:
            runtime_write_client = _build_control_plane_client(
                request,
                require_bearer=True,
                allow_api_token_fallback=False,
            )
        except Exception:
            runtime_write_client = sdk_client

    if runtime_write_client is not None:
        try:
            runtime_write_client.write_event(
                {
                    "event_type": "runtime.invoke.completed",
                    "agent_id": scoped_agent_id,
                    "organization_id": scoped_org_id,
                    "run_id": scoped_run_id,
                    "trace_id": str(req.trace.trace_id).strip(),
                    "conversation_id": chat_id,
                    "request_id": str(req.trace.span_id).strip() or scoped_run_id,
                    "idempotency_key": event_idempotency_key,
                    "payload": {
                        "status": "ok",
                        "workflow_id": workflow_result.get("workflow_id"),
                        "terminal_node": workflow_result.get("terminal_node"),
                        "trace_id": req.trace.trace_id,
                        "trace_url": trace_url,
                        "event_counts": chat_message_metadata.get("event_counts", {}),
                        "nerdstats": chat_message_metadata.get("nerdstats"),
                        "metrics": token_metrics,
                    },
                }
            )
            write_from_workflow = getattr(
                runtime_write_client, "write_chat_message_from_workflow_result", None
            )
            if callable(write_from_workflow):
                write_from_workflow(
                    agent_id=scoped_agent_id,
                    organization_id=scoped_org_id,
                    run_id=scoped_run_id,
                    role="assistant",
                    workflow_result=workflow_result,
                    trace_id=str(req.trace.trace_id).strip(),
                    span_id=str(req.trace.span_id).strip(),
                    tenant_id=str(req.trace.tenant_id).strip(),
                    chat_id=chat_id,
                    message_id=message_id,
                    direction="outbound",
                    created_at_ms=now_ms,
                    idempotency_key=chat_idempotency_key,
                )
            else:
                runtime_write_client.write_chat_message(
                    ChatMessageWrite(
                        agent_id=scoped_agent_id,
                        organization_id=scoped_org_id,
                        run_id=scoped_run_id,
                        chat_id=chat_id,
                        message_id=message_id,
                        role="assistant",
                        direction="outbound",
                        content=chat_message_content,
                        metadata=chat_message_metadata,
                        idempotency_key=chat_idempotency_key,
                        created_at_ms=now_ms,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("runtime SDK write skipped: %s", exc)
        finally:
            if runtime_write_client is not sdk_client:
                runtime_write_client.close()

    return {
        "schema_version": "v1",
        "run_id": scoped_run_id,
        "status": "ok",
        "output": {
            "reply": terminal_output,
            "workflow_id": workflow_result.get("workflow_id"),
            "terminal_node": workflow_result.get("terminal_node"),
            "trace_url": trace_url,
            "nerdstats": nerdstats_payload,
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
