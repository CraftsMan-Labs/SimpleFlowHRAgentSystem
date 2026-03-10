# HR Agent Runtime (Backend)

SimpleFlow-compatible Python runtime with workflow execution backed by SimpleAgents YAML graphs.

## Endpoints

- `GET /health`
- `GET /meta`
- `POST /invoke`
- `POST /webhook`
- `POST /queue/enqueue`
- `POST /queue/process`

Control-plane gateway endpoints (frontend-facing):

- `POST /api/control-plane/auth/sessions`
- `DELETE /api/control-plane/auth/sessions/current`
- `GET /api/control-plane/me`
- `GET /api/control-plane/runtime/registrations`
- `POST /api/control-plane/runtime/invoke`
- `GET /api/control-plane/chat/history/sessions`
- `GET /api/control-plane/chat/history/messages`
- `POST /api/control-plane/chat/history/messages`
- `PATCH /api/control-plane/chat/history/messages/{message_id}`
- `GET /api/agents/available`
- `POST /api/onboarding/start`
- `GET /api/onboarding/status`
- `POST /api/onboarding/retry`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

If local SDKs are not published in your environment, use editable installs from sibling repos:

```bash
pip install -e ../../SimpleFlowSDKs/python
pip install -e ../../SimpleAgents/crates/simple-agents-py
```

## Run

```bash
uvicorn app:app --host 0.0.0.0 --port 8091 --reload
```

## Workflow execution env vars

- `WORKFLOW_ROOT` (default `../workflows`)
- `WORKFLOW_ENTRY_FILE` (default `email-chat-orchestrator-with-subgraph-tool.yaml`)
- `SIMPLE_AGENTS_PROVIDER` (default `openai`)
- `CUSTOM_API_BASE` (optional provider base URL)
- `CUSTOM_API_KEY` (required by most providers)

`/invoke` maps incoming input to workflow `input.messages`, executes the configured workflow, and returns terminal output in `output.reply`.

## Invoke trust env vars

Invoke trust is enabled in template examples.

- `RUNTIME_INVOKE_TRUST_ENABLED=true`
- `RUNTIME_INVOKE_TOKEN_ISSUER`
- `RUNTIME_INVOKE_TOKEN_AUDIENCE`
- Preferred (production): `RUNTIME_INVOKE_TOKEN_JWKS_URL` pointing to SimpleFlow runtime JWKS (`/.well-known/runtime-invoke-jwks.json`)
- Legacy fallback: `RUNTIME_INVOKE_TOKEN_SIGNING_KEY` for HS256 shared-secret verification

If both JWKS and signing key are set, JWKS verification is used.

When enabled, `/invoke` requires `Authorization: Bearer <token>`.

## Bootstrap vars

HR runtime is expected to auto-onboard during backend startup. Set:

- `RUNTIME_BOOTSTRAP_REGISTER_ON_STARTUP=true`
- `RUNTIME_BOOTSTRAP_VALIDATE_REGISTRATION=true`
- `RUNTIME_BOOTSTRAP_ACTIVATE_REGISTRATION=true`
- `RUNTIME_BOOTSTRAP_RUNTIME_ID`
- `RUNTIME_BOOTSTRAP_ENDPOINT_URL`

## Optional control-plane env vars

- `SIMPLEFLOW_API_BASE_URL`
- `SIMPLEFLOW_CLIENT_ID` (preferred)
- `SIMPLEFLOW_CLIENT_SECRET` (preferred)
- `SIMPLEFLOW_API_TOKEN` (legacy fallback)
- `RUNTIME_AGENT_CATALOG_JSON` (optional JSON array for multi-agent selector)

When configured, runtime emits events/chat/queue writes to SimpleFlow APIs, gateway endpoints proxy control-plane requests through `SimpleFlowClient` with bearer-token passthrough, and onboarding lifecycle (`create -> validate -> activate`) uses SDK lifecycle helpers. With client credentials configured, the SDK auto-fetches short-lived access tokens from `/v1/oauth/token`.

## Runtime CORS requirements

- `RUNTIME_CORS_ALLOW_ORIGINS` is required and validated at startup.
- Wildcard `*` is explicitly rejected.
- Empty values are rejected.

## Tests

```bash
python -m unittest -v test_runtime_template.py
```
