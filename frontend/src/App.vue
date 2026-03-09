<script setup>
import { computed, ref } from 'vue'

const runtimeBaseUrl = ref(import.meta.env.VITE_RUNTIME_BASE_URL || 'http://localhost:8091')
const controlPlaneBaseUrl = ref(import.meta.env.VITE_CONTROL_PLANE_BASE_URL || 'http://localhost:8080')
const controlPlaneToken = ref(import.meta.env.VITE_CONTROL_PLANE_TOKEN || '')
const invokeToken = ref('')

const agentId = ref('sample-agent')
const organizationId = ref('dev-org')
const runtimeMessage = ref('Hello from Vue shell')
const activeTab = ref('runtime')

const webhookPayload = ref('{"event":"example.webhook","data":{"hello":"world"}}')
const queuePayload = ref('{"event":"example.queue","payload":{"priority":"normal"}}')
const registrationId = ref('')

const logs = ref([])
const busyAction = ref('')

const tabs = [
  { id: 'runtime', label: 'Runtime Studio' },
  { id: 'events', label: 'Event Sandbox' },
  { id: 'registration', label: 'Registration Lifecycle' },
]

const canCallControlPlane = computed(() => controlPlaneBaseUrl.value.trim().length > 0)

function traceId() {
  return crypto.randomUUID().replaceAll('-', '')
}

function spanId() {
  return crypto.randomUUID().replaceAll('-', '').slice(0, 16)
}

function recordLog(kind, status, response) {
  const item = {
    id: crypto.randomUUID(),
    kind,
    status,
    at: new Date().toISOString(),
    response,
  }
  logs.value = [item, ...logs.value].slice(0, 16)
}

function headersForRequest(includeInvokeToken = false, includeControlPlaneToken = false) {
  const headers = { 'content-type': 'application/json' }

  if (includeInvokeToken && invokeToken.value.trim().length > 0) {
    headers.authorization = `Bearer ${invokeToken.value.trim()}`
  }

  if (includeControlPlaneToken && controlPlaneToken.value.trim().length > 0) {
    headers.authorization = `Bearer ${controlPlaneToken.value.trim()}`
  }

  return headers
}

async function requestJson(url, options) {
  const response = await fetch(url, options)
  const raw = await response.text()
  let parsed = null

  if (raw.length > 0) {
    try {
      parsed = JSON.parse(raw)
    } catch {
      parsed = raw
    }
  }

  if (!response.ok) {
    throw new Error(`status ${response.status}: ${typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2)}`)
  }

  return {
    status: response.status,
    body: parsed,
  }
}

async function runAction(kind, fn) {
  busyAction.value = kind
  try {
    const result = await fn()
    recordLog(kind, 'ok', result)
  } catch (err) {
    recordLog(kind, 'error', err instanceof Error ? err.message : 'Unknown failure')
  } finally {
    busyAction.value = ''
  }
}

async function invokeRuntime() {
  await runAction('invoke', async () => {
    const body = {
      schema_version: 'v1',
      run_id: crypto.randomUUID(),
      agent_id: agentId.value,
      agent_version: 'v1',
      mode: 'realtime',
      trace: {
        trace_id: traceId(),
        span_id: spanId(),
        tenant_id: organizationId.value,
      },
      input: { message: runtimeMessage.value },
      deadline_ms: 0,
      idempotency_key: crypto.randomUUID(),
    }

    return requestJson(`${runtimeBaseUrl.value}/invoke`, {
      method: 'POST',
      headers: headersForRequest(true, false),
      body: JSON.stringify(body),
    })
  })
}

async function getHealth() {
  await runAction('health', async () => {
    return requestJson(`${runtimeBaseUrl.value}/health`, {
      method: 'GET',
      headers: { accept: 'application/json' },
    })
  })
}

async function getMeta() {
  await runAction('meta', async () => {
    return requestJson(`${runtimeBaseUrl.value}/meta`, {
      method: 'GET',
      headers: { accept: 'application/json' },
    })
  })
}

function parseJson(raw) {
  return JSON.parse(raw)
}

async function sendWebhook() {
  await runAction('webhook', async () => {
    return requestJson(`${runtimeBaseUrl.value}/webhook`, {
      method: 'POST',
      headers: headersForRequest(false, false),
      body: JSON.stringify(parseJson(webhookPayload.value)),
    })
  })
}

async function enqueueQueue() {
  await runAction('queue-enqueue', async () => {
    return requestJson(`${runtimeBaseUrl.value}/queue/enqueue`, {
      method: 'POST',
      headers: headersForRequest(false, false),
      body: JSON.stringify(parseJson(queuePayload.value)),
    })
  })
}

async function processQueue() {
  await runAction('queue-process', async () => {
    return requestJson(`${runtimeBaseUrl.value}/queue/process`, {
      method: 'POST',
      headers: headersForRequest(false, false),
      body: JSON.stringify(parseJson(queuePayload.value)),
    })
  })
}

async function registrationAction(action) {
  if (registrationId.value.trim().length === 0) {
    recordLog(`registration-${action}`, 'error', 'Enter a registration id first.')
    return
  }

  if (!canCallControlPlane.value) {
    recordLog(`registration-${action}`, 'error', 'Set VITE_CONTROL_PLANE_BASE_URL to call control-plane lifecycle APIs.')
    return
  }

  await runAction(`registration-${action}`, async () => {
    return requestJson(
      `${controlPlaneBaseUrl.value}/v1/runtime/registrations/${registrationId.value.trim()}/${action}`,
      {
        method: 'POST',
        headers: headersForRequest(false, true),
      },
    )
  })
}
</script>

<template>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">SimpleFlow HR Agent System</p>
      <h1>Runtime Control Surface</h1>
      <p class="lead">
        A single interface to exercise runtime invoke flows, optional event endpoints, and registration lifecycle controls.
      </p>
    </section>

    <section class="panel config-panel">
      <h2>Connection Settings</h2>
      <div class="grid two-up">
        <label>
          Runtime Base URL
          <input v-model="runtimeBaseUrl" type="text" />
        </label>

        <label>
          Control Plane Base URL
          <input v-model="controlPlaneBaseUrl" type="text" />
        </label>

        <label>
          Control Plane Bearer Token
          <input v-model="controlPlaneToken" type="password" placeholder="Optional for registration actions" />
        </label>

        <label>
          Invoke Bearer Token
          <input v-model="invokeToken" type="password" placeholder="Required when invoke trust is enabled" />
        </label>
      </div>
    </section>

    <section class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        type="button"
        class="tab"
        :class="{ active: activeTab === tab.id }"
        @click="activeTab = tab.id"
      >
        {{ tab.label }}
      </button>
    </section>

    <section v-if="activeTab === 'runtime'" class="grid two-up">
      <article class="panel">
        <h3>Invoke Builder</h3>
        <label>
          Agent ID
          <input v-model="agentId" type="text" />
        </label>
        <label>
          Organization ID
          <input v-model="organizationId" type="text" />
        </label>
        <label>
          Message
          <textarea v-model="runtimeMessage" rows="5" />
        </label>
        <button type="button" :disabled="busyAction.length > 0" @click="invokeRuntime">Invoke Runtime</button>
      </article>

      <article class="panel quick-actions">
        <h3>Quick Runtime Checks</h3>
        <p>Use these checks before any invoke test.</p>
        <div class="stacked-buttons">
          <button type="button" :disabled="busyAction.length > 0" @click="getHealth">GET /health</button>
          <button type="button" :disabled="busyAction.length > 0" @click="getMeta">GET /meta</button>
        </div>
      </article>
    </section>

    <section v-if="activeTab === 'events'" class="grid two-up">
      <article class="panel">
        <h3>Webhook Playground</h3>
        <label>
          JSON payload for /webhook
          <textarea v-model="webhookPayload" rows="8" />
        </label>
        <button type="button" :disabled="busyAction.length > 0" @click="sendWebhook">POST /webhook</button>
      </article>

      <article class="panel">
        <h3>Queue Playground</h3>
        <label>
          JSON payload for queue endpoints
          <textarea v-model="queuePayload" rows="8" />
        </label>
        <div class="stacked-buttons">
          <button type="button" :disabled="busyAction.length > 0" @click="enqueueQueue">POST /queue/enqueue</button>
          <button type="button" :disabled="busyAction.length > 0" @click="processQueue">POST /queue/process</button>
        </div>
      </article>
    </section>

    <section v-if="activeTab === 'registration'" class="grid one-up">
      <article class="panel">
        <h3>Registration Lifecycle</h3>
        <p>
          Calls control-plane lifecycle endpoints for an existing registration id.
          These endpoints are disabled by auth when the bearer token is missing or invalid.
        </p>
        <label>
          Registration ID
          <input v-model="registrationId" type="text" placeholder="runtime-reg-..." />
        </label>
        <div class="inline-buttons">
          <button type="button" :disabled="busyAction.length > 0" @click="registrationAction('validate')">
            Validate
          </button>
          <button type="button" :disabled="busyAction.length > 0" @click="registrationAction('activate')">
            Activate
          </button>
          <button type="button" :disabled="busyAction.length > 0" @click="registrationAction('deactivate')">
            Deactivate
          </button>
        </div>
      </article>
    </section>

    <section class="panel logs">
      <div class="logs-header">
        <h3>Response Console</h3>
        <button type="button" class="ghost" @click="logs = []">Clear</button>
      </div>
      <p v-if="logs.length === 0" class="muted">No requests yet. Run any action to see live responses.</p>

      <article v-for="item in logs" :key="item.id" class="log-item" :class="item.status">
        <header>
          <strong>{{ item.kind }}</strong>
          <span>{{ item.at }}</span>
        </header>
        <pre>{{ typeof item.response === 'string' ? item.response : JSON.stringify(item.response, null, 2) }}</pre>
      </article>
    </section>
  </main>
</template>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Chivo:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

:global(body) {
  margin: 0;
  min-height: 100vh;
  background:
    radial-gradient(circle at 12% 18%, rgba(12, 138, 255, 0.25), transparent 45%),
    radial-gradient(circle at 90% 5%, rgba(255, 127, 80, 0.3), transparent 36%),
    linear-gradient(145deg, #f8f2e7 5%, #f2f4f8 45%, #eef4ff 100%);
  color: #11243d;
}

.shell {
  --ink: #11243d;
  --panel: rgba(255, 255, 255, 0.82);
  --panel-border: rgba(17, 36, 61, 0.16);
  --accent: #1d6eff;
  --accent-strong: #0f52bf;
  --accent-warm: #d04d14;
  --ok: #076739;
  --error: #8f1313;
  width: min(1100px, calc(100vw - 2rem));
  margin: 2rem auto 4rem;
  display: grid;
  gap: 1rem;
  font-family: 'Chivo', 'Trebuchet MS', sans-serif;
}

.hero {
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid var(--panel-border);
  border-radius: 1.2rem;
  padding: 1.25rem 1.3rem;
  backdrop-filter: blur(4px);
}

.eyebrow {
  margin: 0;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-size: 0.75rem;
  color: #455f87;
}

h1,
h2,
h3,
p {
  margin: 0;
}

h1 {
  margin-top: 0.4rem;
  font-size: clamp(1.8rem, 2vw, 2.5rem);
}

.lead {
  margin-top: 0.5rem;
  max-width: 70ch;
  color: #2d4668;
}

.panel {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: 1rem;
  padding: 1rem;
  display: grid;
  gap: 0.75rem;
  box-shadow: 0 8px 18px rgba(17, 36, 61, 0.05);
}

.grid {
  display: grid;
  gap: 1rem;
}

.two-up {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.one-up {
  grid-template-columns: minmax(0, 1fr);
}

label {
  display: grid;
  gap: 0.3rem;
  font-size: 0.9rem;
  font-weight: 600;
}

input,
textarea {
  font: inherit;
  border: 1px solid rgba(17, 36, 61, 0.22);
  border-radius: 0.65rem;
  padding: 0.68rem 0.75rem;
  background: rgba(255, 255, 255, 0.9);
}

textarea {
  resize: vertical;
  min-height: 96px;
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: 0.82rem;
}

button {
  font: inherit;
  font-weight: 600;
  border: 0;
  border-radius: 999px;
  padding: 0.6rem 0.95rem;
  cursor: pointer;
  color: white;
  background: linear-gradient(130deg, var(--accent) 5%, var(--accent-strong) 85%);
  transition: transform 0.12s ease, box-shadow 0.12s ease;
}

button:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 14px rgba(16, 83, 190, 0.25);
}

button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
  transform: none;
}

.ghost {
  background: transparent;
  color: var(--ink);
  border: 1px solid rgba(17, 36, 61, 0.3);
}

.tabs {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.tab {
  background: rgba(255, 255, 255, 0.66);
  color: #1b3762;
  border: 1px solid rgba(17, 36, 61, 0.15);
}

.tab.active {
  color: white;
  background: linear-gradient(130deg, var(--accent) 5%, var(--accent-warm) 90%);
}

.stacked-buttons,
.inline-buttons {
  display: flex;
  gap: 0.55rem;
  flex-wrap: wrap;
}

.quick-actions p {
  color: #314d73;
}

.logs {
  gap: 0.8rem;
}

.logs-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.muted {
  color: #445d80;
}

.log-item {
  border-radius: 0.8rem;
  border: 1px solid rgba(17, 36, 61, 0.12);
  background: rgba(255, 255, 255, 0.74);
  padding: 0.75rem;
  display: grid;
  gap: 0.5rem;
}

.log-item header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: 0.84rem;
}

.log-item.ok {
  border-color: rgba(7, 103, 57, 0.35);
}

.log-item.error {
  border-color: rgba(143, 19, 19, 0.4);
}

pre {
  margin: 0;
  padding: 0.65rem;
  border-radius: 0.6rem;
  background: rgba(17, 36, 61, 0.04);
  border: 1px solid rgba(17, 36, 61, 0.1);
  white-space: pre-wrap;
  overflow-x: auto;
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: 0.78rem;
  line-height: 1.45;
}

@media (max-width: 960px) {
  .two-up {
    grid-template-columns: minmax(0, 1fr);
  }

  .shell {
    margin-top: 1rem;
  }
}
</style>
