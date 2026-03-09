# SimpleFlowHRAgentSystem

Standalone test repo for an HR-focused SimpleFlow runtime built from SimpleFlow templates, running in Docker only.

## What this repo includes

- `backend/`: Python runtime service compatible with SimpleFlow runtime contract, plus a local control-plane gateway built on `SimpleFlowClient`.
- `frontend/`: Vue chat shell that talks only to the template backend (`/api/control-plane/*`) for sign-in, preflight, invoke, and chat history.
- `workflows/`: YAML workflows copied from prior SimpleAgentChatTemplate setup.

## Runtime contract

- `GET /health`
- `GET /meta`
- `POST /invoke`

## Template backend control-plane gateway

The frontend never calls control-plane URLs directly. It calls backend-local gateway routes:

- `POST /api/control-plane/auth/sessions`
- `DELETE /api/control-plane/auth/sessions/current`
- `GET /api/control-plane/me`
- `GET /api/control-plane/runtime/registrations`
- `POST /api/control-plane/runtime/invoke`
- `GET /api/control-plane/chat/history/sessions`
- `GET /api/control-plane/chat/history/messages`
- `POST /api/control-plane/chat/history/messages`
- `PATCH /api/control-plane/chat/history/messages/{message_id}`

Backend then calls control-plane APIs through `SimpleFlowClient` with incoming bearer-token passthrough.

Canonical onboarding endpoints exposed by template backend:

- `GET /api/agents/available`
- `POST /api/onboarding/start`
- `GET /api/onboarding/status`
- `POST /api/onboarding/retry`

Additional helper endpoints:

- `POST /webhook`
- `POST /queue/enqueue`
- `POST /queue/process`

## Quick start

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
docker compose build hr-backend hr-frontend
make up
```

Open:

```bash
http://localhost:8092/health
http://localhost:5175
```

Use `make logs` to stream both services and `make down` to stop them.

## Container startup optimization

- Backend image now preinstalls OS/Python dependencies during `docker build`.
- Frontend image now preinstalls `node_modules` during `docker build`.
- Runtime container commands are now lightweight and skip `apt-get`/`pip install`/`npm install` on every start.
- `./backend`, `./frontend`, `./workflows`, and sibling SDK/agent repos are still mounted for local editable development.

## Control-plane-first chat flow

- Frontend sign-in uses `POST /api/control-plane/auth/sessions` and stores the access token locally.
- Frontend first selects from backend-provided agents and then uses onboarding status (`start/status/retry`) until selected agent is `active`.
- Chat invoke uses `POST /api/control-plane/runtime/invoke` only (no direct browser call to runtime `/invoke`).
- Chat sessions persist with stable `chat_id` values and use:
  - `GET /api/control-plane/chat/history/sessions`
  - `GET /api/control-plane/chat/history/messages`
  - `POST /api/control-plane/chat/history/messages`
  - `PATCH /api/control-plane/chat/history/messages/{message_id}`

## Workflows

Configured by env vars in `backend/.env`:

- `WORKFLOW_ROOT=/workspace/workflows`
- `WORKFLOW_ENTRY_FILE=email-chat-orchestrator-with-subgraph-tool.yaml`

`/invoke` converts runtime input into workflow `input.messages`, executes the selected YAML graph via `simple_agents_py`, and returns terminal output as `output.reply`.

If provider credentials are not set (`CUSTOM_API_KEY` or provider-specific env vars), `/invoke` returns `503` with a configuration hint.

## Local verification

```bash
docker compose up -d --build hr-backend hr-frontend
make test-backend
make build-frontend
curl -sS http://localhost:8092/health
curl -sS http://localhost:5175
```

## Control-plane linking

This repo joins `simpleflow_default` Docker network. When registering runtime in SimpleFlow control-plane, use:

- `endpoint_url`: `http://simpleflow-hr-runtime:8091`

This avoids host-network routing issues from control-plane backend containers.

Required networking notes:

- Start the SimpleFlow stack first so `simpleflow_default` exists.
- Keep runtime and control-plane backends on the same Docker network.
- Do not register runtime with localhost URL when control-plane backend is containerized.

## Notes

- Start the SimpleFlow stack first so external Docker network `simpleflow_default` exists.
- Docker build context is set to `../..` so backend/frontend images can include sibling repos at build time:
  - `../../SimpleFlowSDKs/python`
  - `../../SimpleAgents/crates/simple-agents-py`
