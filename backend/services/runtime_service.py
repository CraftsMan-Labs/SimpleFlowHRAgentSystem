from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from config import RuntimeSettings
from runtime_helpers.workflow import build_workflow_messages


class RuntimeService:
    def __init__(self, settings: RuntimeSettings) -> None:
        self._settings = settings
        self.workflow_path = settings.workflow_root / settings.workflow_entry_file

    def load_workflow_client(self) -> Any:
        try:
            from simple_agents_py import Client
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail="simple_agents_py is not installed; install it before invoking workflows",
            ) from exc

        if self._settings.custom_api_key != "":
            os.environ.setdefault("CUSTOM_API_KEY", self._settings.custom_api_key)
        if self._settings.custom_api_base != "":
            os.environ.setdefault("CUSTOM_API_BASE", self._settings.custom_api_base)

        try:
            return Client(
                self._settings.simple_agents_provider,
                api_base=(
                    self._settings.custom_api_base
                    if self._settings.custom_api_base != ""
                    else None
                ),
                api_key=(
                    self._settings.custom_api_key
                    if self._settings.custom_api_key != ""
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=(
                    "simple_agents_py client failed to initialize; set CUSTOM_API_KEY "
                    "or provider-specific credentials"
                ),
            ) from exc

    def build_workflow_registry(self) -> dict[str, str]:
        subgraph_path = self._settings.workflow_root / "hr-warning-email-subgraph.yaml"
        if not subgraph_path.exists():
            return {}

        resolved = str(subgraph_path)
        return {
            "hr_warning_email_subgraph": resolved,
            "hr-warning-email-subgraph": resolved,
        }

    def run_agent_workflow(self, req: Any) -> dict[str, Any]:
        if not self.workflow_path.exists():
            raise HTTPException(
                status_code=500,
                detail=f"workflow file not found: {self.workflow_path}",
            )

        workflow_client = self.load_workflow_client()
        workflow_input = {
            "messages": build_workflow_messages(req.input),
            "email_text": str(req.input.get("message", "")).strip(),
            "workflow_registry": self.build_workflow_registry(),
        }
        workflow_options = {
            "trace": {"tenant": {"run_id": req.run_id}},
            "telemetry": {"nerdstats": True},
        }
        try:
            return workflow_client.run_workflow_yaml(
                str(self.workflow_path),
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
