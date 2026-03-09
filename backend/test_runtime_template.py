from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))


def _install_test_stubs() -> None:
    if "jwt" not in sys.modules:
        jwt_stub = types.ModuleType("jwt")

        class _PyJWKClient:
            def __init__(self, url: str) -> None:
                self.url = url

            def get_signing_key_from_jwt(self, token: str):
                class _Key:
                    key = "stub"

                return _Key()

        def _decode(*args, **kwargs):
            return {}

        setattr(jwt_stub, "PyJWKClient", _PyJWKClient)
        setattr(jwt_stub, "decode", _decode)
        sys.modules["jwt"] = jwt_stub

    if "fastapi" not in sys.modules:
        fastapi_stub = types.ModuleType("fastapi")
        fastapi_middleware_stub = types.ModuleType("fastapi.middleware")
        fastapi_cors_stub = types.ModuleType("fastapi.middleware.cors")

        class HTTPException(Exception):
            def __init__(self, status_code: int, detail: str) -> None:
                self.status_code = status_code
                self.detail = detail
                super().__init__(detail)

        class Request:
            def __init__(self) -> None:
                self.headers: dict[str, str] = {}

        class FastAPI:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def add_middleware(self, *args, **kwargs):
                return None

            def get(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

            def post(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

            def delete(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

            def patch(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

            def on_event(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

        setattr(fastapi_stub, "FastAPI", FastAPI)
        setattr(fastapi_stub, "HTTPException", HTTPException)
        setattr(fastapi_stub, "Request", Request)
        setattr(fastapi_cors_stub, "CORSMiddleware", object)
        sys.modules["fastapi"] = fastapi_stub
        sys.modules["fastapi.middleware"] = fastapi_middleware_stub
        sys.modules["fastapi.middleware.cors"] = fastapi_cors_stub

    if "pydantic" not in sys.modules:
        pydantic_stub = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        def Field(default=None, default_factory=None):
            if default_factory is not None:
                return default_factory()
            return default

        setattr(pydantic_stub, "BaseModel", BaseModel)
        setattr(pydantic_stub, "Field", Field)
        sys.modules["pydantic"] = pydantic_stub

    if "simpleflow_sdk" not in sys.modules:
        sdk_stub = types.ModuleType("simpleflow_sdk")

        class _SimpleType:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        class InvokeTokenVerifier:
            def __init__(self, **kwargs):
                pass

            def verify(self, token: str, key: str):
                return {}

        class SimpleFlowClient:
            def __init__(self, *args, **kwargs):
                self._base_url = "http://stub"

                class _Client:
                    @staticmethod
                    def delete(url: str, headers=None):
                        class _Response:
                            status_code = 200
                            text = "{}"

                            @staticmethod
                            def json():
                                return {}

                        return _Response()

                self._client = _Client()

            def close(self):
                return None

            def _get(self, path: str):
                return {}

            def _post(self, path: str, payload):
                return {}

            def _patch(self, path: str, payload):
                return {}

            def register_runtime(self, registration):
                return {}

            def validate_runtime_registration(self, registration_id: str):
                return {}

            def activate_runtime_registration(self, registration_id: str):
                return None

            def ensure_runtime_registration_active(self, registration):
                return {"registration_id": "reg_123", "status": "active"}

            def report_runtime_event(self, event):
                return None

            def write_chat_message(self, message):
                return None

            def publish_queue_contract(self, contract):
                return None

        setattr(sdk_stub, "ChatMessageWrite", _SimpleType)
        setattr(sdk_stub, "QueueContract", _SimpleType)
        setattr(sdk_stub, "RuntimeEvent", _SimpleType)
        setattr(sdk_stub, "RuntimeRegistration", _SimpleType)
        setattr(sdk_stub, "SimpleFlowClient", SimpleFlowClient)
        setattr(sdk_stub, "InvokeTokenVerifier", InvokeTokenVerifier)
        setattr(sdk_stub, "SimpleFlowAuthenticationError", RuntimeError)
        setattr(sdk_stub, "SimpleFlowAuthorizationError", RuntimeError)
        setattr(sdk_stub, "SimpleFlowLifecycleError", RuntimeError)
        sys.modules["simpleflow_sdk"] = sdk_stub

    if "simple_agents_py" not in sys.modules:
        agents_stub = types.ModuleType("simple_agents_py")

        class Client:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def run_workflow_yaml(self, workflow_path, workflow_input, **kwargs):
                return {
                    "workflow_id": "email-chat-orchestrator-with-subgraph-tool",
                    "terminal_node": "respond_casual",
                    "terminal_output": {
                        "subject": "HR Follow-up",
                        "body": "Draft body",
                    },
                    "workflow_path": workflow_path,
                    "input": workflow_input,
                }

        setattr(agents_stub, "Client", Client)
        sys.modules["simple_agents_py"] = agents_stub


class RuntimeTemplateTests(unittest.TestCase):
    def test_trust_config_requires_issuer_when_enabled(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "true"
        os.environ.pop("RUNTIME_INVOKE_TOKEN_ISSUER", None)
        os.environ["RUNTIME_INVOKE_TOKEN_AUDIENCE"] = "runtime"
        os.environ["RUNTIME_INVOKE_TOKEN_SIGNING_KEY"] = "secret"
        os.environ.pop("RUNTIME_INVOKE_TOKEN_JWKS_URL", None)

        with self.assertRaises(ValueError):
            runtime_app._build_invoke_trust_config()

    def test_scope_prefers_verified_claims(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        req = runtime_app.InvokeRequest(
            schema_version="v1",
            run_id="run_from_request",
            agent_id="sample-python-runtime",
            agent_version="v1",
            mode="realtime",
            trace=runtime_app.InvokeTrace(
                trace_id="trace_1", span_id="span_1", tenant_id="tenant_request"
            ),
            input={},
        )
        scope = runtime_app.InvokeScope(
            agent_id="agent_from_claim",
            org_id="org_from_claim",
            user_id="user_from_claim",
            run_id="run_from_claim",
        )

        resolved = runtime_app._resolve_invoke_scope(req, scope)
        self.assertEqual(
            resolved,
            (
                "agent_from_claim",
                "run_from_claim",
                "org_from_claim",
                "user_from_claim",
            ),
        )

    def test_trust_config_allows_jwks_and_signing_key(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "true"
        os.environ["RUNTIME_INVOKE_TOKEN_ISSUER"] = "simpleflow-runtime"
        os.environ["RUNTIME_INVOKE_TOKEN_AUDIENCE"] = "simpleflow-runtime"
        os.environ["RUNTIME_INVOKE_TOKEN_SIGNING_KEY"] = "legacy-secret"
        os.environ["RUNTIME_INVOKE_TOKEN_JWKS_URL"] = (
            "http://backend:8080/.well-known/runtime-invoke-jwks.json"
        )

        config = runtime_app._build_invoke_trust_config()
        self.assertEqual(
            config.jwks_url, "http://backend:8080/.well-known/runtime-invoke-jwks.json"
        )
        self.assertEqual(config.signing_key, "legacy-secret")

    def test_verify_invoke_prefers_jwks_when_configured(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)

        class _SharedVerifier:
            called = False

            def verify(self, token: str, key: str):
                self.called = True
                raise AssertionError("shared verifier should not be called")

        class _JWKSClient:
            called = False

            def get_signing_key_from_jwt(self, token: str):
                self.called = True

                class _Key:
                    key = "jwks-key"

                return _Key()

        setattr(
            runtime_app,
            "invoke_trust_config",
            runtime_app.InvokeTrustConfig(
                enabled=True,
                issuer="simpleflow-runtime",
                audience="simpleflow-runtime",
                signing_key="legacy-secret",
                jwks_url="http://backend:8080/.well-known/runtime-invoke-jwks.json",
            ),
        )
        setattr(runtime_app, "shared_key_verifier", _SharedVerifier())
        setattr(runtime_app, "jwks_client", _JWKSClient())
        runtime_app.jwt.decode = lambda *args, **kwargs: {
            "agent_id": "agent_1",
            "org_id": "org_1",
            "user_id": "user_1",
            "run_id": "run_1",
        }

        request = runtime_app.Request()
        request.headers = {"authorization": "Bearer test-token"}

        scope = runtime_app._verify_invoke_request(request)
        self.assertEqual(scope.agent_id, "agent_1")
        jwks_client = getattr(runtime_app, "jwks_client")
        shared_key_verifier = getattr(runtime_app, "shared_key_verifier")
        self.assertTrue(jwks_client.called)
        self.assertFalse(shared_key_verifier.called)

    def test_build_workflow_messages_prefers_messages_array(self) -> None:
        _install_test_stubs()
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        payload = {
            "message": "fallback",
            "messages": [
                {"role": "user", "content": "Need HR warning draft"},
                {"role": "assistant", "content": "Please provide details"},
            ],
        }
        result = runtime_app._build_workflow_messages(payload)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["content"], "Need HR warning draft")

    def test_workflow_text_output_formats_subject_body(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        text = runtime_app._workflow_text_output(
            {"subject": "Policy Reminder", "body": "Please improve attendance."}
        )
        self.assertIn("Subject: Policy Reminder", text)
        self.assertIn("Please improve attendance.", text)

    def test_build_agent_catalog_uses_primary_agent(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        os.environ["RUNTIME_AGENT_ID"] = "hr-agent-runtime"
        os.environ["RUNTIME_AGENT_VERSION"] = "v1"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        catalog = runtime_app._build_agent_catalog()
        self.assertGreaterEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["agent_id"], "hr-agent-runtime")

    def test_onboarding_default_state_not_started(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        state = runtime_app._get_or_create_onboarding_state(
            runtime_app.agent_id,
            runtime_app.agent_version,
        )
        self.assertEqual(state["state"], "not_started")
        self.assertEqual(state["steps"]["create"], "pending")

    def test_normalize_registration_id_handles_case_variants(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        self.assertEqual(
            runtime_app._normalize_registration_id({"RegistrationID": "reg_upper"}),
            "reg_upper",
        )
        self.assertEqual(
            runtime_app._normalize_registration_id({"registrationId": "reg_camel"}),
            "reg_camel",
        )

    def test_sync_onboarding_marks_active_for_case_variant_status(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        state = runtime_app._onboarding_state_from_catalog(
            {
                "agent_id": runtime_app.agent_id,
                "agent_version": runtime_app.agent_version,
                "runtime_id": "",
                "endpoint_url": "",
            }
        )
        original_get = runtime_app._control_plane_get
        setattr(
            runtime_app,
            "_control_plane_get",
            lambda *args, **kwargs: {
                "Registrations": [
                    {
                        "RegistrationID": "reg_case",
                        "Status": "ACTIVE",
                    }
                ]
            },
        )
        try:
            result = runtime_app._sync_onboarding_state_from_control_plane(
                state,
                runtime_app.Request(),
            )
        finally:
            setattr(runtime_app, "_control_plane_get", original_get)

        self.assertEqual(result["state"], "active")
        self.assertEqual(result["registration_id"], "reg_case")

    def test_sync_onboarding_not_started_only_when_no_registration_exists(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        state = runtime_app._onboarding_state_from_catalog(
            {
                "agent_id": runtime_app.agent_id,
                "agent_version": runtime_app.agent_version,
                "runtime_id": "",
                "endpoint_url": "",
            }
        )
        original_get = runtime_app._control_plane_get
        setattr(
            runtime_app,
            "_control_plane_get",
            lambda *args, **kwargs: {"registrations": [{"id": "reg_unknown"}]},
        )
        try:
            result = runtime_app._sync_onboarding_state_from_control_plane(
                state,
                runtime_app.Request(),
            )
        finally:
            setattr(runtime_app, "_control_plane_get", original_get)

        self.assertEqual(result["state"], "in_progress")
        self.assertEqual(result["registration_id"], "reg_unknown")

    def test_control_plane_me_normalizes_user_and_org_ids(self) -> None:
        _install_test_stubs()
        os.environ["RUNTIME_INVOKE_TRUST_ENABLED"] = "false"
        import app as runtime_app

        runtime_app = importlib.reload(runtime_app)
        original_get = runtime_app._control_plane_get
        setattr(
            runtime_app,
            "_control_plane_get",
            lambda *args, **kwargs: {
                "user": {
                    "UserID": "user_123",
                    "OrganizationID": "org_abc",
                }
            },
        )
        try:
            result = runtime_app.control_plane_me(runtime_app.Request())
        finally:
            setattr(runtime_app, "_control_plane_get", original_get)

        self.assertEqual(result["user_id"], "user_123")
        self.assertEqual(result["organization_id"], "org_abc")


if __name__ == "__main__":
    unittest.main()
