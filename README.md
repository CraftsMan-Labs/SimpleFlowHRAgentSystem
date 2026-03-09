# SimpleFlowHRAgentSystem

Standalone test repo for an HR-focused SimpleFlow runtime built from SimpleFlow templates, running in Docker only.

## What this repo includes

- `backend/`: Python runtime service compatible with SimpleFlow runtime contract.
- `frontend/`: Vue operator shell for invoke, events, and registration lifecycle calls.
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
make smoke
```

## Control-plane linking

This repo joins `simpleflow_default` Docker network. When registering runtime in SimpleFlow control-plane, use:

- `endpoint_url`: `http://simpleflow-hr-runtime:8091`

This avoids host-network routing issues from control-plane backend containers.

## Notes

- Start the SimpleFlow stack first so external Docker network `simpleflow_default` exists.
- `make up` installs backend dependencies in-container from sibling repos:
  - `../../SimpleFlowSDKs/python`
  - `../../SimpleAgents/crates/simple-agents-py`
