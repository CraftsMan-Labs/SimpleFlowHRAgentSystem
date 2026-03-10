from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode

from fastapi import HTTPException, Request


@dataclass(slots=True)
class ControlPlaneConfig:
    api_base_url: str
    api_token: str


def read_auth_bearer_token(request: Request) -> str:
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


def build_control_plane_client(
    request: Request,
    *,
    config: ControlPlaneConfig,
    client_cls: Callable[..., Any],
    require_bearer: bool = False,
    allow_api_token_fallback: bool = True,
) -> Any:
    if config.api_base_url == "":
        raise HTTPException(
            status_code=503,
            detail="control plane is not configured; set SIMPLEFLOW_API_BASE_URL",
        )

    bearer_token = read_auth_bearer_token(request)
    if require_bearer and bearer_token == "":
        raise HTTPException(status_code=401, detail="authorization required")

    selected_token = bearer_token
    if selected_token == "" and allow_api_token_fallback:
        selected_token = config.api_token
    return client_cls(config.api_base_url, selected_token)


def control_plane_query_path(path: str, query: dict[str, Any]) -> str:
    encoded = urlencode(query)
    if encoded == "":
        return path
    return f"{path}?{encoded}"


def map_control_plane_error(exc: Exception) -> HTTPException:
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


def control_plane_get(
    request: Request,
    *,
    path: str,
    config: ControlPlaneConfig,
    client_cls: Callable[..., Any],
    require_bearer: bool = False,
) -> dict[str, Any]:
    client = build_control_plane_client(
        request,
        config=config,
        client_cls=client_cls,
        require_bearer=require_bearer,
    )
    try:
        return client._get(path)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise map_control_plane_error(exc) from exc
    finally:
        client.close()


def control_plane_post(
    request: Request,
    *,
    path: str,
    payload: dict[str, Any],
    config: ControlPlaneConfig,
    client_cls: Callable[..., Any],
    require_bearer: bool = False,
    allow_api_token_fallback: bool = True,
) -> dict[str, Any]:
    client = build_control_plane_client(
        request,
        config=config,
        client_cls=client_cls,
        require_bearer=require_bearer,
        allow_api_token_fallback=allow_api_token_fallback,
    )
    try:
        return client._post(path, payload)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise map_control_plane_error(exc) from exc
    finally:
        client.close()


def control_plane_patch(
    request: Request,
    *,
    path: str,
    payload: dict[str, Any],
    config: ControlPlaneConfig,
    client_cls: Callable[..., Any],
    require_bearer: bool = False,
) -> dict[str, Any]:
    client = build_control_plane_client(
        request,
        config=config,
        client_cls=client_cls,
        require_bearer=require_bearer,
    )
    try:
        return client._patch(path, payload)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise map_control_plane_error(exc) from exc
    finally:
        client.close()


def control_plane_delete(
    request: Request,
    *,
    path: str,
    config: ControlPlaneConfig,
    client_cls: Callable[..., Any],
    require_bearer: bool = False,
) -> dict[str, Any]:
    client = build_control_plane_client(
        request,
        config=config,
        client_cls=client_cls,
        require_bearer=require_bearer,
    )
    try:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{config.api_base_url.rstrip('/')}{normalized_path}"
        headers: dict[str, str] = {}
        token = read_auth_bearer_token(request)
        if token != "":
            headers["Authorization"] = f"Bearer {token}"
        elif config.api_token != "":
            headers["Authorization"] = f"Bearer {config.api_token}"

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
        raise map_control_plane_error(exc) from exc
    finally:
        client.close()


def normalize_control_plane_me(
    payload: dict[str, Any],
    read_non_empty_string: Callable[[Any], str],
) -> dict[str, Any]:
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
        user_id = read_non_empty_string(candidate)
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
        organization_id = read_non_empty_string(candidate)
        if organization_id != "":
            break

    if user_id != "":
        normalized["id"] = user_id
        normalized["user_id"] = user_id
    if organization_id != "":
        normalized["organization_id"] = organization_id
    return normalized
