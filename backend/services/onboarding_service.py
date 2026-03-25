from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request
from simpleflow_sdk import (
    SimpleFlowAuthenticationError,
    SimpleFlowAuthorizationError,
    SimpleFlowClient,
    SimpleFlowLifecycleError,
)

from config import RuntimeSettings
from services.control_plane_service import ControlPlaneService


class OnboardingService:
    def __init__(
        self,
        *,
        settings: RuntimeSettings,
        control_plane: ControlPlaneService,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._control_plane = control_plane
        self._logger = logger
        self._onboarding_states: dict[str, dict[str, Any]] = {}
        self._agent_catalog = self._build_agent_catalog()

    @property
    def agent_catalog(self) -> list[dict[str, Any]]:
        return self._agent_catalog

    @property
    def onboarding_states(self) -> dict[str, dict[str, Any]]:
        return self._onboarding_states

    def _agent_key(self, runtime_agent_id: str, runtime_agent_version: str) -> str:
        return f"{runtime_agent_id.strip()}::{runtime_agent_version.strip()}"

    def _build_agent_catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        fallback_endpoint = self._settings.runtime_public_base_url.strip()
        primary_endpoint = self._settings.runtime_bootstrap_endpoint_url.strip()
        if primary_endpoint == "":
            primary_endpoint = fallback_endpoint

        primary = {
            "agent_id": self._settings.runtime_agent_id,
            "agent_version": self._settings.runtime_agent_version,
            "runtime_id": self._settings.runtime_bootstrap_runtime_id
            or self._settings.runtime_agent_id,
            "endpoint_url": primary_endpoint,
            "enabled": True,
        }
        catalog.append(primary)

        raw = self._settings.runtime_agent_catalog_json.strip()
        if raw != "":
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, list):
                    for item in decoded:
                        if not isinstance(item, dict):
                            continue
                        runtime_agent_id = str(item.get("agent_id", "")).strip()
                        runtime_agent_version = str(
                            item.get("agent_version", "")
                        ).strip()
                        if runtime_agent_id == "" or runtime_agent_version == "":
                            continue
                        endpoint_url = str(item.get("endpoint_url", "")).strip()
                        if endpoint_url == "":
                            endpoint_url = fallback_endpoint
                        catalog.append(
                            {
                                "agent_id": runtime_agent_id,
                                "agent_version": runtime_agent_version,
                                "runtime_id": str(item.get("runtime_id", "")).strip()
                                or runtime_agent_id,
                                "endpoint_url": endpoint_url,
                                "enabled": bool(item.get("enabled", True)),
                            }
                        )
            except json.JSONDecodeError:
                self._logger.warning(
                    "invalid RUNTIME_AGENT_CATALOG_JSON; ignoring value"
                )

        deduped: dict[str, dict[str, Any]] = {}
        for item in catalog:
            key = self._agent_key(str(item["agent_id"]), str(item["agent_version"]))
            deduped[key] = item
        return [entry for entry in deduped.values() if bool(entry.get("enabled", True))]

    def find_agent_config(
        self,
        runtime_agent_id: str,
        runtime_agent_version: str,
    ) -> dict[str, Any] | None:
        key = self._agent_key(runtime_agent_id, runtime_agent_version)
        for item in self._agent_catalog:
            if (
                self._agent_key(
                    str(item.get("agent_id", "")),
                    str(item.get("agent_version", "")),
                )
                == key
            ):
                return item
        return None

    def require_known_agent(
        self, runtime_agent_id: str, runtime_agent_version: str
    ) -> None:
        if self.find_agent_config(runtime_agent_id, runtime_agent_version) is not None:
            return
        raise HTTPException(status_code=404, detail="agent is not available in catalog")

    def require_known_agent_id(self, runtime_agent_id: str) -> None:
        trimmed = runtime_agent_id.strip()
        for item in self._agent_catalog:
            if str(item.get("agent_id", "")).strip() == trimmed:
                return
        raise HTTPException(status_code=404, detail="agent is not available in catalog")

    def _read_non_empty_string(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return ""
        return str(value).strip()

    def _read_string_field(self, payload: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            if key in payload:
                value = self._read_non_empty_string(payload.get(key))
                if value != "":
                    return value
        return ""

    def normalize_registration_id(self, payload: dict[str, Any]) -> str:
        return self._read_string_field(
            payload,
            (
                "registration_id",
                "registrationId",
                "RegistrationID",
                "id",
                "ID",
            ),
        )

    def _normalize_registration_status(self, payload: dict[str, Any]) -> str:
        raw_status = self._read_string_field(
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

    def _normalize_runtime_registration(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "registration_id": self.normalize_registration_id(item),
            "status": self._normalize_registration_status(item),
            "raw": item,
        }

    def _normalize_runtime_registrations_payload(
        self,
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
                normalized.append(self._normalize_runtime_registration(item))
        return normalized

    def normalize_control_plane_me(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._control_plane.normalize_me(payload, self._read_non_empty_string)

    def onboarding_state_from_catalog(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
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

    def get_or_create_onboarding_state(
        self,
        runtime_agent_id: str,
        runtime_agent_version: str,
    ) -> dict[str, Any]:
        key = self._agent_key(runtime_agent_id, runtime_agent_version)
        existing = self._onboarding_states.get(key)
        if existing is not None:
            return existing

        config = self.find_agent_config(runtime_agent_id, runtime_agent_version)
        if config is None:
            raise HTTPException(
                status_code=404, detail="agent is not available in catalog"
            )
        state = self.onboarding_state_from_catalog(config)
        self._onboarding_states[key] = state
        return state

    def onboarding_public_view(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "onboarding_id": state.get("onboarding_id", ""),
            "agent_id": state.get("agent_id", ""),
            "agent_version": state.get("agent_version", ""),
            "state": state.get("state", "not_started"),
            "message": state.get("message", ""),
            "registration_id": state.get("registration_id", ""),
            "steps": state.get("steps", {}),
        }

    def sync_onboarding_state_from_control_plane(
        self,
        state: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        runtime_agent_id = str(state.get("agent_id", "")).strip()
        runtime_agent_version = str(state.get("agent_version", "")).strip()
        if runtime_agent_id == "" or runtime_agent_version == "":
            return state

        path = self._control_plane.query_path(
            "/v1/runtime/registrations",
            {
                "agent_id": runtime_agent_id,
                "agent_version": runtime_agent_version,
            },
        )

        try:
            payload = self._control_plane.get(request, path=path, require_bearer=True)
        except HTTPException:
            return state

        registrations = self._normalize_runtime_registrations_payload(payload)
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
        registration_id = self._read_non_empty_string(selected.get("registration_id"))
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

    def _build_machine_control_plane_client(self) -> SimpleFlowClient:
        api_base_url = self._settings.simpleflow_api_base_url
        if api_base_url == "":
            raise HTTPException(
                status_code=503,
                detail="control plane is not configured; set SIMPLEFLOW_API_BASE_URL",
            )
        if (
            self._settings.simpleflow_client_id != ""
            and self._settings.simpleflow_client_secret != ""
        ):
            return SimpleFlowClient(
                api_base_url,
                oauth_client_id=self._settings.simpleflow_client_id,
                oauth_client_secret=self._settings.simpleflow_client_secret,
                runtime_register_path=self._settings.simpleflow_runtime_register_path,
            )
        if self._settings.simpleflow_api_token == "":
            raise HTTPException(
                status_code=503,
                detail="machine credentials are required; set SIMPLEFLOW_CLIENT_ID and SIMPLEFLOW_CLIENT_SECRET",
            )
        return SimpleFlowClient(
            api_base_url,
            self._settings.simpleflow_api_token,
            runtime_register_path=self._settings.simpleflow_runtime_register_path,
        )

    def _uses_runtime_connect_path(self) -> bool:
        path = self._settings.simpleflow_runtime_register_path.strip().lower()
        return path.endswith("/runtime/connect") or path.endswith("/connect")

    def run_onboarding_lifecycle(
        self,
        runtime_agent_id: str,
        runtime_agent_version: str,
    ) -> dict[str, Any]:
        self.require_known_agent(runtime_agent_id, runtime_agent_version)
        state = self.get_or_create_onboarding_state(
            runtime_agent_id, runtime_agent_version
        )
        state["state"] = "in_progress"
        state["message"] = "Running runtime registration lifecycle."
        state["steps"] = {
            "create": "pending",
            "validate": "pending",
            "activate": "pending",
        }

        config = self.find_agent_config(runtime_agent_id, runtime_agent_version)
        if config is None:
            raise HTTPException(
                status_code=404, detail="agent is not available in catalog"
            )

        uses_connect = self._uses_runtime_connect_path()
        registration = {
            "agent_id": runtime_agent_id,
            "agent_version": runtime_agent_version,
            "endpoint_url": str(config.get("endpoint_url", "")).strip(),
            "auth_mode": "jwt",
            "capabilities": ["chat", "webhook", "queue"],
            "runtime_id": str(config.get("runtime_id", "")).strip(),
        }
        if not uses_connect:
            registration["execution_mode"] = "remote_runtime"

        endpoint_url = str(registration.get("endpoint_url", "")).strip()
        if endpoint_url == "":
            state["state"] = "failed"
            state["message"] = "runtime endpoint URL is required for onboarding"
            state["steps"]["create"] = "failed"
            return state

        client = self._build_machine_control_plane_client()
        try:
            result: dict[str, Any]
            ensure_registration_active = getattr(
                client,
                "ensure_runtime_registration_active",
                None,
            )
            if not uses_connect and callable(ensure_registration_active):
                ensured = ensure_registration_active(
                    registration=registration,
                )
                if not isinstance(ensured, dict):
                    raise HTTPException(
                        status_code=502,
                        detail="registration lifecycle response must be an object",
                    )
                result = ensured
            elif uses_connect:
                state["steps"]["create"] = "running"
                created = client.register_runtime(
                    registration,
                )
                registration_id = self.normalize_registration_id(created)
                if registration_id != "":
                    state["registration_id"] = registration_id
                result = {
                    "registration_id": registration_id,
                    "created": True,
                    "validated": False,
                    "activated": False,
                }
            else:
                state["steps"]["create"] = "running"
                created = client.register_runtime(
                    registration,
                )
                registration_id = self.normalize_registration_id(created)
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

                validation = client.validate_runtime_registration(
                    registration_id,
                )
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
                client.activate_runtime_registration(
                    registration_id,
                )
                result = {
                    "registration_id": registration_id,
                    "created": True,
                    "validated": True,
                    "activated": True,
                }

            registration_id = self.normalize_registration_id(result)
            if registration_id == "" and not uses_connect:
                state["state"] = "failed"
                state["steps"]["create"] = "failed"
                raise HTTPException(
                    status_code=502,
                    detail="registration response missing registration id",
                )

            state["registration_id"] = registration_id
            state["steps"]["create"] = (
                "success" if result.get("created") is True else "skipped"
            )
            if uses_connect:
                state["steps"]["validate"] = "skipped"
                state["steps"]["activate"] = "skipped"
            else:
                state["steps"]["validate"] = (
                    "success" if result.get("validated") is True else "skipped"
                )
                state["steps"]["activate"] = (
                    "success" if result.get("activated") is True else "skipped"
                )
        except SimpleFlowAuthenticationError as exc:
            state["state"] = "blocked"
            state["message"] = "machine client auth failed for runtime connect"
            state["steps"]["create"] = "failed"
            return state
        except SimpleFlowAuthorizationError as exc:
            state["state"] = "blocked"
            state["message"] = (
                "machine client is forbidden for runtime connect; verify runtime.connect permission and that RUNTIME_BOOTSTRAP_RUNTIME_ID matches the machine client RuntimeID"
            )
            state["steps"]["create"] = "failed"
            return state
        except SimpleFlowLifecycleError as exc:
            state["state"] = "failed"
            state["message"] = str(exc)
            return state
        except Exception as exc:  # noqa: BLE001
            state["state"] = "failed"
            state["message"] = str(exc)
            return state
        finally:
            client.close()

        state["registration_id"] = str(result.get("registration_id", "")).strip()
        state["state"] = "active"
        state["message"] = "Runtime registration is active."
        return state
