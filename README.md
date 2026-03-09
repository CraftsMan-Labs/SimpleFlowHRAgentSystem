# SimpleFlowHRAgentSystem

Standalone test repo for an HR-focused SimpleFlow runtime built from SimpleFlow templates, running in Docker only.

## What this repo includes

- `backend/`: Python runtime service compatible with SimpleFlow runtime contract.
- `frontend/`: Vue chat shell that requires control-plane sign-in, registration preflight, and control-plane invoke/history APIs.
- `workflows/`: YAML workflows copied from prior SimpleAgentChatTemplate setup.

## Runtime contract

- `GET /health`
- `GET /meta`
- `POST /invoke`

Additional helper endpoints:

- `POST /webhook`
- `POST /queue/enqueue`
- `POST /queue/process`

## Quick start

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
make up
```

Open:

```bash
http://localhost:8092/health
http://localhost:5175
```

Use `make logs` to stream both services and `make down` to stop them.

## Control-plane-first chat flow

- Frontend sign-in uses `POST /v1/auth/sessions` and stores the access token locally.
- Chat is disabled until preflight finds an `active` registration for the selected `(agent_id, agent_version)`.
- Chat invoke uses `POST /v1/runtime/invoke` only (no direct browser call to runtime `/invoke`).
- Chat sessions persist with stable `chat_id` values and use:
  - `GET /v1/chat/history/sessions`
  - `GET /v1/chat/history/messages`
  - `POST /v1/chat/history/messages`
  - `PATCH /v1/chat/history/messages/{message_id}`

## Workflows

Configured by env vars in `backend/.env`:

- `WORKFLOW_ROOT=/workspace/workflows`
- `WORKFLOW_ENTRY_FILE=email-chat-orchestrator-with-subgraph-tool.yaml`

`/invoke` converts runtime input into workflow `input.messages`, executes the selected YAML graph via `simple_agents_py`, and returns terminal output as `output.reply`.

If provider credentials are not set (`CUSTOM_API_KEY` or provider-specific env vars), `/invoke` returns `503` with a configuration hint.

## Local verification

```bash
make test-backend
make build-frontend
curl -sS http://localhost:8092/health
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
- `make up` installs backend dependencies in-container from sibling repos:
  - `../../SimpleFlowSDKs/python`
  - `../../SimpleAgents/crates/simple-agents-py`
