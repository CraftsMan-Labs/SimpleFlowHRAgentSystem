from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class RuntimeSettings(BaseSettings):
    runtime_cors_allow_origins: str = Field(default="")

    runtime_agent_id: str = Field(default="sample-python-runtime")
    runtime_agent_version: str = Field(default="v1")
    runtime_agent_catalog_json: str = Field(default="")

    simpleflow_api_base_url: str = Field(default="")
    simpleflow_api_token: str = Field(default="")
    simpleflow_client_id: str = Field(default="")
    simpleflow_client_secret: str = Field(default="")

    workflow_root: Path = Field(
        default_factory=lambda: (
            Path(__file__).resolve().parent.parent / "workflows"
        ).resolve()
    )
    workflow_entry_file: str = Field(
        default="email-chat-orchestrator-with-subgraph-tool.yaml"
    )
    simple_agents_provider: str = Field(default="openai")
    custom_api_base: str = Field(default="")
    custom_api_key: str = Field(default="")

    runtime_invoke_trust_enabled: bool = Field(default=False)
    runtime_invoke_token_issuer: str = Field(default="")
    runtime_invoke_token_audience: str = Field(default="")
    runtime_invoke_token_jwks_url: str = Field(default="")

    runtime_bootstrap_register_on_startup: bool = Field(default=False)
    runtime_bootstrap_validate_registration: bool = Field(default=False)
    runtime_bootstrap_activate_registration: bool = Field(default=False)
    runtime_bootstrap_execution_mode: str = Field(default="remote_runtime")
    runtime_bootstrap_endpoint_url: str = Field(default="")
    runtime_bootstrap_auth_mode: str = Field(default="jwt")
    runtime_bootstrap_runtime_id: str = Field(default="")
    runtime_bootstrap_registration_id: str = Field(default="")

    @property
    def cors_allow_origins(self) -> list[str]:
        return parse_cors_allow_origins(self.runtime_cors_allow_origins)


def _env_bool(name: str, fallback: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw == "":
        return fallback
    return raw in {"1", "true", "yes", "on"}


def parse_cors_allow_origins(value: str) -> list[str]:
    normalized = value.strip()
    if normalized == "":
        raise ValueError("RUNTIME_CORS_ALLOW_ORIGINS must not be empty")
    parsed = [item.strip() for item in normalized.split(",") if item.strip() != ""]
    if len(parsed) == 0:
        raise ValueError("RUNTIME_CORS_ALLOW_ORIGINS must include at least one origin")
    if any(item == "*" for item in parsed):
        raise ValueError("Wildcard CORS origin '*' is not allowed")
    return parsed


@lru_cache(maxsize=1)
def get_settings() -> RuntimeSettings:
    settings = RuntimeSettings(
        runtime_cors_allow_origins=os.getenv("RUNTIME_CORS_ALLOW_ORIGINS", "").strip(),
        runtime_agent_id=os.getenv("RUNTIME_AGENT_ID", "sample-python-runtime").strip(),
        runtime_agent_version=os.getenv("RUNTIME_AGENT_VERSION", "v1").strip(),
        runtime_agent_catalog_json=os.getenv("RUNTIME_AGENT_CATALOG_JSON", "").strip(),
        simpleflow_api_base_url=os.getenv("SIMPLEFLOW_API_BASE_URL", "").strip(),
        simpleflow_api_token=os.getenv("SIMPLEFLOW_API_TOKEN", "").strip(),
        simpleflow_client_id=os.getenv("SIMPLEFLOW_CLIENT_ID", "").strip(),
        simpleflow_client_secret=os.getenv("SIMPLEFLOW_CLIENT_SECRET", "").strip(),
        workflow_root=Path(
            os.getenv(
                "WORKFLOW_ROOT",
                str((Path(__file__).resolve().parent.parent / "workflows").resolve()),
            )
        ),
        workflow_entry_file=os.getenv(
            "WORKFLOW_ENTRY_FILE",
            "email-chat-orchestrator-with-subgraph-tool.yaml",
        ).strip(),
        simple_agents_provider=(
            os.getenv("SIMPLE_AGENTS_PROVIDER", "openai").strip() or "openai"
        ),
        custom_api_base=os.getenv("CUSTOM_API_BASE", "").strip(),
        custom_api_key=os.getenv("CUSTOM_API_KEY", "").strip(),
        runtime_invoke_trust_enabled=_env_bool("RUNTIME_INVOKE_TRUST_ENABLED", False),
        runtime_invoke_token_issuer=os.getenv(
            "RUNTIME_INVOKE_TOKEN_ISSUER", ""
        ).strip(),
        runtime_invoke_token_audience=os.getenv(
            "RUNTIME_INVOKE_TOKEN_AUDIENCE", ""
        ).strip(),
        runtime_invoke_token_jwks_url=os.getenv(
            "RUNTIME_INVOKE_TOKEN_JWKS_URL",
            "",
        ).strip(),
        runtime_bootstrap_register_on_startup=_env_bool(
            "RUNTIME_BOOTSTRAP_REGISTER_ON_STARTUP",
            False,
        ),
        runtime_bootstrap_validate_registration=_env_bool(
            "RUNTIME_BOOTSTRAP_VALIDATE_REGISTRATION",
            False,
        ),
        runtime_bootstrap_activate_registration=_env_bool(
            "RUNTIME_BOOTSTRAP_ACTIVATE_REGISTRATION",
            False,
        ),
        runtime_bootstrap_execution_mode=(
            os.getenv("RUNTIME_BOOTSTRAP_EXECUTION_MODE", "remote_runtime").strip()
            or "remote_runtime"
        ),
        runtime_bootstrap_endpoint_url=os.getenv(
            "RUNTIME_BOOTSTRAP_ENDPOINT_URL",
            "",
        ).strip(),
        runtime_bootstrap_auth_mode=(
            os.getenv("RUNTIME_BOOTSTRAP_AUTH_MODE", "jwt").strip() or "jwt"
        ),
        runtime_bootstrap_runtime_id=os.getenv(
            "RUNTIME_BOOTSTRAP_RUNTIME_ID", ""
        ).strip(),
        runtime_bootstrap_registration_id=os.getenv(
            "RUNTIME_BOOTSTRAP_REGISTRATION_ID",
            "",
        ).strip(),
    )
    parse_cors_allow_origins(settings.runtime_cors_allow_origins)
    return settings
