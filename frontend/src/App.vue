<script setup>
import { computed, ref } from 'vue'

const runtimeBaseUrl = ref(import.meta.env.VITE_RUNTIME_BASE_URL || 'http://localhost:8092')
const invokeToken = ref('')

const agentId = ref('57253b0a-3cd5-4d5f-b89c-730591b838d7')
const agentVersion = ref('v1')
const tenantId = ref('2081ccab-6b15-4396-a8b9-e2bfa717783e')

const draft = ref('')
const busy = ref(false)
const error = ref('')
const showConfig = ref(false)

const conversation = ref([
  {
    id: crypto.randomUUID(),
    role: 'assistant',
    text: 'Hi. I am the HR drafting assistant. Tell me the situation and I will draft the warning email.'
  }
])

const canSend = computed(() => draft.value.trim() !== '' && busy.value === false)

function uuid() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `run-${Date.now()}`
}

function pushMessage(role, text) {
  conversation.value.push({ id: crypto.randomUUID(), role, text })
}

function parseReply(payload) {
  if (payload && typeof payload === 'object') {
    if (typeof payload?.output?.reply === 'string') {
      return payload.output.reply
    }
    if (typeof payload?.reply === 'string') {
      return payload.reply
    }
  }
  return JSON.stringify(payload, null, 2)
}

async function sendMessage() {
  const text = draft.value.trim()
  if (text === '' || busy.value) {
    return
  }

  error.value = ''
  pushMessage('user', text)
  draft.value = ''
  busy.value = true

  const body = {
    schema_version: 'v1',
    run_id: uuid(),
    agent_id: agentId.value.trim(),
    agent_version: agentVersion.value.trim(),
    mode: 'realtime',
    trace: {
      trace_id: `trace-${Date.now()}`,
      span_id: `span-${Date.now()}`,
      tenant_id: tenantId.value.trim()
    },
    input: { message: text },
    deadline_ms: 0,
    idempotency_key: uuid()
  }

  try {
    const headers = { 'content-type': 'application/json' }
    if (invokeToken.value.trim() !== '') {
      headers.authorization = `Bearer ${invokeToken.value.trim()}`
    }

    const response = await fetch(`${runtimeBaseUrl.value.replace(/\/$/, '')}/invoke`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body)
    })

    const raw = await response.text()
    let parsed = raw
    try {
      parsed = JSON.parse(raw)
    } catch (_e) {
      parsed = raw
    }

    if (!response.ok) {
      throw new Error(typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2))
    }

    pushMessage('assistant', parseReply(parsed))
  } catch (e) {
    const message = e instanceof Error ? e.message : 'Invoke failed'
    error.value = message
    pushMessage('assistant', `I could not complete that request.\n\n${message}`)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="chat-shell">
    <header class="chat-header">
      <div>
        <p class="kicker">SimpleFlow HR Agent System</p>
        <h1>HR Chat</h1>
      </div>
      <button type="button" class="ghost" @click="showConfig = !showConfig">
        {{ showConfig ? 'Hide Settings' : 'Settings' }}
      </button>
    </header>

    <section v-if="showConfig" class="config">
      <label>
        Runtime Base URL
        <input v-model="runtimeBaseUrl" type="text" />
      </label>
      <label>
        Agent ID
        <input v-model="agentId" type="text" />
      </label>
      <label>
        Agent Version
        <input v-model="agentVersion" type="text" />
      </label>
      <label>
        Tenant ID
        <input v-model="tenantId" type="text" />
      </label>
      <label>
        Invoke Bearer Token (optional)
        <input v-model="invokeToken" type="password" />
      </label>
    </section>

    <section class="messages">
      <article v-for="msg in conversation" :key="msg.id" class="bubble" :class="msg.role">
        <strong>{{ msg.role === 'assistant' ? 'HR Agent' : 'You' }}</strong>
        <pre>{{ msg.text }}</pre>
      </article>
    </section>

    <footer class="composer">
      <textarea
        v-model="draft"
        rows="3"
        placeholder="Describe the HR issue and ask for draft email..."
        @keydown.enter.exact.prevent="sendMessage"
      />
      <div class="composer-actions">
        <p v-if="error !== ''" class="error">{{ error }}</p>
        <button type="button" :disabled="!canSend" @click="sendMessage">
          {{ busy ? 'Sending...' : 'Send' }}
        </button>
      </div>
    </footer>
  </main>
</template>

<style scoped>
:global(body) {
  margin: 0;
  background: linear-gradient(140deg, #f4f7fb 0%, #eef5ff 100%);
  color: #10243f;
}

.chat-shell {
  width: min(960px, calc(100vw - 2rem));
  margin: 1.2rem auto;
  display: grid;
  gap: 0.9rem;
  font-family: 'Chivo', 'Trebuchet MS', sans-serif;
}

.chat-header,
.config,
.messages,
.composer {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(16, 36, 63, 0.14);
  border-radius: 14px;
  padding: 0.9rem;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.kicker {
  margin: 0;
  font-size: 0.75rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #3d5f90;
}

h1 { margin: 0.2rem 0 0; }

.config {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.6rem;
}

label { display: grid; gap: 0.25rem; font-weight: 600; font-size: 0.9rem; }
input, textarea {
  font: inherit;
  border: 1px solid rgba(16, 36, 63, 0.2);
  border-radius: 10px;
  padding: 0.6rem 0.7rem;
}

.messages {
  min-height: 52vh;
  max-height: 62vh;
  overflow: auto;
  display: grid;
  gap: 0.65rem;
}

.bubble {
  max-width: 82%;
  padding: 0.65rem;
  border-radius: 10px;
  border: 1px solid rgba(16, 36, 63, 0.12);
  background: #f5f8fd;
}

.bubble.user {
  margin-left: auto;
  background: #dfeaff;
}

.bubble.assistant {
  margin-right: auto;
  background: #f6fbf2;
}

pre {
  margin: 0.3rem 0 0;
  white-space: pre-wrap;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.82rem;
}

.composer { display: grid; gap: 0.5rem; }

.composer-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

button {
  border: 0;
  border-radius: 999px;
  padding: 0.55rem 1rem;
  cursor: pointer;
  color: white;
  background: #1f62da;
}

.ghost {
  background: transparent;
  border: 1px solid rgba(16, 36, 63, 0.3);
  color: #163664;
}

.error { margin: 0; color: #8b1f1f; font-weight: 600; font-size: 0.85rem; }

@media (max-width: 860px) {
  .config { grid-template-columns: minmax(0, 1fr); }
  .bubble { max-width: 95%; }
}
</style>
