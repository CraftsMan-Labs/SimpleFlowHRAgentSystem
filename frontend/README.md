# Vue Frontend Shell

Shared frontend shell for remote runtime integrations.

## Run

```bash
npm install
npm run dev
```

## Environment

- `VITE_RUNTIME_BASE_URL` (default: `http://localhost:8091`)
- `VITE_CONTROL_PLANE_BASE_URL` (default: `http://localhost:8080`)
- `VITE_CONTROL_PLANE_TOKEN` (optional)

Copy `.env.example` to `.env` and adjust values as needed.

## Included UIs

- Runtime Studio (`GET /health`, `GET /meta`, `POST /invoke`)
- Event Sandbox (`POST /webhook`, `POST /queue/enqueue`, `POST /queue/process`)
- Registration Lifecycle (`POST /v1/runtime/registrations/{id}/validate|activate|deactivate`)
