from __future__ import annotations

from typing import Any

from fastapi import Request
from simpleflow_sdk import SimpleFlowClient

from runtime_helpers.control_plane import (
    ControlPlaneConfig,
    build_control_plane_client,
    control_plane_delete,
    control_plane_get,
    control_plane_patch,
    control_plane_post,
    control_plane_query_path,
    map_control_plane_error,
    normalize_control_plane_me,
    read_auth_bearer_token,
)


class ControlPlaneService:
    def __init__(self, config: ControlPlaneConfig) -> None:
        self._config = config

    def read_auth_bearer_token(self, request: Request) -> str:
        return read_auth_bearer_token(request)

    def build_client(
        self,
        request: Request,
        *,
        require_bearer: bool = False,
        allow_api_token_fallback: bool = True,
    ) -> SimpleFlowClient:
        return build_control_plane_client(
            request,
            config=self._config,
            client_cls=SimpleFlowClient,
            require_bearer=require_bearer,
            allow_api_token_fallback=allow_api_token_fallback,
        )

    def query_path(self, path: str, query: dict[str, Any]) -> str:
        return control_plane_query_path(path, query)

    def map_error(self, exc: Exception):
        return map_control_plane_error(exc)

    def get(
        self,
        request: Request,
        *,
        path: str,
        require_bearer: bool = False,
    ) -> dict[str, Any]:
        return control_plane_get(
            request,
            path=path,
            config=self._config,
            client_cls=SimpleFlowClient,
            require_bearer=require_bearer,
        )

    def post(
        self,
        request: Request,
        *,
        path: str,
        payload: dict[str, Any],
        require_bearer: bool = False,
        allow_api_token_fallback: bool = True,
    ) -> dict[str, Any]:
        return control_plane_post(
            request,
            path=path,
            payload=payload,
            config=self._config,
            client_cls=SimpleFlowClient,
            require_bearer=require_bearer,
            allow_api_token_fallback=allow_api_token_fallback,
        )

    def patch(
        self,
        request: Request,
        *,
        path: str,
        payload: dict[str, Any],
        require_bearer: bool = False,
    ) -> dict[str, Any]:
        return control_plane_patch(
            request,
            path=path,
            payload=payload,
            config=self._config,
            client_cls=SimpleFlowClient,
            require_bearer=require_bearer,
        )

    def delete(
        self,
        request: Request,
        *,
        path: str,
        require_bearer: bool = False,
    ) -> dict[str, Any]:
        return control_plane_delete(
            request,
            path=path,
            config=self._config,
            client_cls=SimpleFlowClient,
            require_bearer=require_bearer,
        )

    def normalize_me(self, payload: dict[str, Any], read_non_empty_string):
        return normalize_control_plane_me(payload, read_non_empty_string)
