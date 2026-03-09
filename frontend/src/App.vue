<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import craftsmanLogo from './assets/CraftsmanLabs.svg'
import craftsmanLogoWhite from './assets/CraftsmanLabs-white.svg'
import { clearAccessToken, readAccessToken, storeAccessToken } from './authToken'

const themeStorageKey = 'simpleflow-hr-theme'

const templateBackendBaseUrl = ref(import.meta.env.VITE_TEMPLATE_BACKEND_BASE_URL || 'http://localhost:8092')
const agentId = ref(import.meta.env.VITE_AGENT_ID || '')
const agentVersion = ref(import.meta.env.VITE_AGENT_VERSION || 'v1')
const selectedAgentKey = ref('')
const availableAgents = ref([])

const email = ref('')
const password = ref('')
const authState = ref('signed-out')
const authNotice = ref('Sign in to use control-plane invoke and chat history.')
const authBusy = ref(false)

const currentTheme = ref('dark')
const me = ref(null)
const accessToken = ref('')
const showConfig = ref(false)

const registrationGate = reactive({
  state: 'not_started',
  message: 'Sign in to start onboarding.',
  registrationId: ''
})
const onboardingBusy = ref(false)

const sessions = ref([])
const activeChatId = ref('')
const conversation = ref([])
const draft = ref('')
const requestBusy = ref(false)
const error = ref('')

const canSend = computed(() => {
  return (
    authState.value === 'signed-in' &&
    registrationGate.state === 'ready' &&
    requestBusy.value === false &&
    draft.value.trim() !== '' &&
    activeChatId.value.trim() !== ''
  )
})

const activeLogo = computed(() => (currentTheme.value === 'dark' ? craftsmanLogoWhite : craftsmanLogo))

const sortedSessions = computed(() => {
  return [...sessions.value].sort((a, b) => {
    const aTs = Date.parse(a.lastMessageAt || '') || 0
    const bTs = Date.parse(b.lastMessageAt || '') || 0
    return bTs - aTs
  })
})

function uuid() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `id-${Date.now()}`
}

function parseJsonSafe(raw) {
  if (typeof raw !== 'string') {
    return raw
  }
  const trimmed = raw.trim()
  if (trimmed === '') {
    return ''
  }
  try {
    return JSON.parse(trimmed)
  } catch (_error) {
    return raw
  }
}

function readCookie(name) {
  if (typeof document === 'undefined') {
    return ''
  }
  const rows = document.cookie.split(';')
  for (const row of rows) {
    const [rawName, ...rest] = row.split('=')
    if (rawName.trim() === name) {
      return decodeURIComponent(rest.join('='))
    }
  }
  return ''
}

function csrfTokenFromCookies() {
  const candidates = ['sf_csrf_token', 'csrf_cookie', 'csrf_token', 'csrfToken']
  for (const item of candidates) {
    const value = readCookie(item).trim()
    if (value !== '') {
      return value
    }
  }
  return ''
}

function normalizeError(parsed, response) {
  if (typeof parsed === 'string' && parsed.trim() !== '') {
    return parsed
  }
  if (parsed && typeof parsed === 'object') {
    if (typeof parsed.error === 'string' && parsed.error.trim() !== '') {
      return parsed.error
    }
    if (typeof parsed.detail === 'string' && parsed.detail.trim() !== '') {
      return parsed.detail
    }
    if (typeof parsed.message === 'string' && parsed.message.trim() !== '') {
      return parsed.message
    }
  }
  return response.statusText || 'Request failed'
}

async function requestControlPlane(path, options = {}) {
  const method = options.method || 'GET'
  const body = options.body
  const headers = { ...(options.headers || {}) }
  const token = accessToken.value.trim()
  const url = `${templateBackendBaseUrl.value.replace(/\/$/, '')}${path}`

  if (options.requiresAuth !== false && token !== '') {
    headers.Authorization = `Bearer ${token}`
  }

  const isMutation = method !== 'GET' && method !== 'HEAD'
  if (isMutation) {
    const csrf = csrfTokenFromCookies()
    if (csrf !== '' && headers['X-CSRF-Token'] === undefined) {
      headers['X-CSRF-Token'] = csrf
    }
  }

  let payload
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  const response = await fetch(url, {
    method,
    credentials: 'include',
    headers,
    body: payload
  })

  const raw = await response.text()
  const parsed = parseJsonSafe(raw)
  if (!response.ok) {
    if (response.status === 401) {
      clearLocalSession('Session expired. Sign in again.')
    }
    throw new Error(normalizeError(parsed, response))
  }
  return parsed
}

function applyTheme(nextTheme) {
  const normalized = nextTheme === 'light' ? 'light' : 'dark'
  currentTheme.value = normalized
  document.documentElement.setAttribute('data-theme', normalized)
}

function toggleTheme() {
  const next = currentTheme.value === 'dark' ? 'light' : 'dark'
  applyTheme(next)
  window.localStorage.setItem(themeStorageKey, next)
}

function initializeTheme() {
  const stored = window.localStorage.getItem(themeStorageKey)
  if (stored === 'light' || stored === 'dark') {
    applyTheme(stored)
    return
  }
  const prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches
  applyTheme(prefersLight ? 'light' : 'dark')
}

function clearLocalSession(notice = 'Signed out.') {
  accessToken.value = ''
  me.value = null
  sessions.value = []
  availableAgents.value = []
  selectedAgentKey.value = ''
  activeChatId.value = ''
  conversation.value = []
  registrationGate.state = 'unknown'
  registrationGate.message = 'Sign in to validate runtime registration.'
  registrationGate.registrationId = ''
  authState.value = 'signed-out'
  authNotice.value = notice
  clearAccessToken()
}

function readUserId() {
  if (!me.value || typeof me.value !== 'object') {
    return ''
  }
  const candidates = [me.value.id, me.value.user_id, me.value.userId]
  for (const item of candidates) {
    if (typeof item === 'string' && item.trim() !== '') {
      return item.trim()
    }
  }
  return ''
}

function readOrgId() {
  if (!me.value || typeof me.value !== 'object') {
    return 'local-org'
  }
  const candidates = [me.value.organization_id, me.value.organizationId]
  for (const item of candidates) {
    if (typeof item === 'string' && item.trim() !== '') {
      return item.trim()
    }
  }
  return 'local-org'
}

function pushConversationMessage(role, text, messageId = uuid()) {
  conversation.value.push({
    id: uuid(),
    role,
    text,
    messageId,
    createdAt: new Date().toISOString()
  })
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
  if (typeof payload === 'string') {
    return payload
  }
  try {
    return JSON.stringify(payload, null, 2)
  } catch (_error) {
    return 'No assistant reply returned.'
  }
}

function parseContentText(rawContent) {
  if (typeof rawContent === 'string') {
    const parsed = parseJsonSafe(rawContent)
    if (parsed && typeof parsed === 'object') {
      if (typeof parsed.text === 'string' && parsed.text.trim() !== '') {
        return parsed.text
      }
      if (typeof parsed.message === 'string' && parsed.message.trim() !== '') {
        return parsed.message
      }
    }
    return rawContent
  }

  if (rawContent && typeof rawContent === 'object') {
    if (typeof rawContent.text === 'string' && rawContent.text.trim() !== '') {
      return rawContent.text
    }
    if (typeof rawContent.message === 'string' && rawContent.message.trim() !== '') {
      return rawContent.message
    }
  }

  return ''
}

function normalizeStatus(item) {
  const candidates = [item?.status, item?.Status]
  for (const value of candidates) {
    if (typeof value === 'string' && value.trim() !== '') {
      return value.trim().toLowerCase()
    }
  }
  return ''
}

function normalizeSession(item) {
  return {
    chatId: item?.chat_id || item?.chatId || item?.ChatID || '',
    title: item?.title || item?.Title || 'Untitled chat',
    lastMessageAt: item?.last_message_at || item?.lastMessageAt || item?.LastMessageAt || new Date().toISOString(),
    status: normalizeStatus(item)
  }
}

function agentOptionKey(agent) {
  const id = typeof agent?.agent_id === 'string' ? agent.agent_id.trim() : ''
  const version = typeof agent?.agent_version === 'string' ? agent.agent_version.trim() : ''
  return `${id}::${version}`
}

function applySelectedAgentKey() {
  const [nextAgentId, nextAgentVersion] = selectedAgentKey.value.split('::')
  agentId.value = (nextAgentId || '').trim()
  agentVersion.value = (nextAgentVersion || '').trim() || 'v1'
}

async function fetchAvailableAgents() {
  const payload = await requestControlPlane('/api/agents/available', { method: 'GET' })
  const items = Array.isArray(payload?.agents) ? payload.agents : []
  availableAgents.value = items.filter((item) => {
    return typeof item?.agent_id === 'string' && item.agent_id.trim() !== ''
  })

  if (availableAgents.value.length === 0) {
    selectedAgentKey.value = ''
    agentId.value = ''
    agentVersion.value = 'v1'
    return
  }

  const currentKey = _agentKey(agentId.value, agentVersion.value)
  const matching = availableAgents.value.find((item) => agentOptionKey(item) === currentKey)
  if (matching) {
    selectedAgentKey.value = currentKey
    return
  }

  const defaultAgent = payload?.default_agent
  const defaultKey = _agentKey(defaultAgent?.agent_id || '', defaultAgent?.agent_version || '')
  const fallback = availableAgents.value.find((item) => agentOptionKey(item) === defaultKey) || availableAgents.value[0]
  selectedAgentKey.value = agentOptionKey(fallback)
  applySelectedAgentKey()
}

function _agentKey(id, version) {
  return `${String(id || '').trim()}::${String(version || '').trim()}`
}

async function fetchMe() {
  const payload = await requestControlPlane('/api/control-plane/me', { method: 'GET' })
  me.value = payload
}

async function preflightRegistration() {
  if (authState.value !== 'signed-in') {
    registrationGate.state = 'not_started'
    registrationGate.message = 'Sign in to start onboarding.'
    registrationGate.registrationId = ''
    return
  }

  const trimmedAgentId = agentId.value.trim()
  const trimmedVersion = agentVersion.value.trim()
  if (trimmedAgentId === '' || trimmedVersion === '') {
    registrationGate.state = 'missing'
    registrationGate.message = 'No agent is selected.'
    registrationGate.registrationId = ''
    return
  }

  registrationGate.state = 'checking'
  registrationGate.message = 'Checking onboarding status...'
  registrationGate.registrationId = ''

  try {
    const response = await requestControlPlane(
      `/api/onboarding/status?agent_id=${encodeURIComponent(trimmedAgentId)}&agent_version=${encodeURIComponent(trimmedVersion)}`,
      { method: 'GET' }
    )
    const state = typeof response?.state === 'string' ? response.state.trim().toLowerCase() : 'not_started'
    const message = typeof response?.message === 'string' ? response.message : ''
    registrationGate.registrationId = response?.registration_id || ''

    if (state === 'active') {
      registrationGate.state = 'ready'
      registrationGate.message = message || 'Onboarding is complete. Chat is enabled.'
      return
    }
    if (state === 'in_progress') {
      registrationGate.state = 'checking'
      registrationGate.message = message || 'Onboarding is running.'
      return
    }
    if (state === 'blocked') {
      registrationGate.state = 'unauthorized'
      registrationGate.message = message || 'Onboarding is blocked by auth or scope policy.'
      return
    }
    if (state === 'failed') {
      registrationGate.state = 'error'
      registrationGate.message = message || 'Onboarding failed. Retry to continue.'
      return
    }

    registrationGate.state = 'missing'
    registrationGate.message = message || 'Onboarding has not started yet.'
  } catch (requestError) {
    const message = requestError instanceof Error ? requestError.message : 'Registration preflight failed.'
    if (message.toLowerCase().includes('unauthorized')) {
      registrationGate.state = 'unauthorized'
      registrationGate.message = 'Unauthorized session. Sign in again.'
    } else {
      registrationGate.state = 'error'
      registrationGate.message = message
    }
  }
}

async function recheckRegistration() {
  error.value = ''
  if (authState.value !== 'signed-in') {
    registrationGate.state = 'unknown'
    registrationGate.message = 'Sign in first, then recheck registration.'
    authNotice.value = 'Sign in is required before registration checks.'
    return
  }
  await preflightRegistration()
}

async function startOnboarding() {
  if (authState.value !== 'signed-in' || onboardingBusy.value) {
    return
  }
  onboardingBusy.value = true
  error.value = ''
  try {
    await requestControlPlane('/api/onboarding/start', {
      method: 'POST',
      body: {
        agent_id: agentId.value.trim(),
        agent_version: agentVersion.value.trim()
      }
    })
    await preflightRegistration()
  } catch (requestError) {
    error.value = requestError instanceof Error ? requestError.message : 'Onboarding start failed.'
  } finally {
    onboardingBusy.value = false
  }
}

async function retryOnboarding() {
  if (authState.value !== 'signed-in' || onboardingBusy.value) {
    return
  }
  onboardingBusy.value = true
  error.value = ''
  try {
    await requestControlPlane('/api/onboarding/retry', {
      method: 'POST',
      body: {
        agent_id: agentId.value.trim(),
        agent_version: agentVersion.value.trim()
      }
    })
    await preflightRegistration()
  } catch (requestError) {
    error.value = requestError instanceof Error ? requestError.message : 'Onboarding retry failed.'
  } finally {
    onboardingBusy.value = false
  }
}

function upsertSession(chatId, title, timestamp) {
  const trimmed = chatId.trim()
  if (trimmed === '') {
    return
  }
  const idx = sessions.value.findIndex((item) => item.chatId === trimmed)
  if (idx < 0) {
    sessions.value.push({
      chatId: trimmed,
      title: title.trim() || 'Untitled chat',
      status: 'active',
      lastMessageAt: timestamp
    })
    return
  }
  sessions.value[idx] = {
    ...sessions.value[idx],
    title: sessions.value[idx].title || title.trim() || 'Untitled chat',
    lastMessageAt: timestamp,
    status: 'active'
  }
}

async function loadSessions() {
  const userId = readUserId()
  const trimmedAgentId = agentId.value.trim()
  if (userId === '' || trimmedAgentId === '') {
    sessions.value = []
    activeChatId.value = ''
    return
  }

  const payload = await requestControlPlane(
    `/api/control-plane/chat/history/sessions?agent_id=${encodeURIComponent(trimmedAgentId)}&user_id=${encodeURIComponent(userId)}&status=active&limit=50`,
    { method: 'GET' }
  )
  const items = Array.isArray(payload?.sessions) ? payload.sessions.map(normalizeSession) : []
  sessions.value = items.filter((item) => item.chatId !== '')
  if (sessions.value.length > 0 && activeChatId.value.trim() === '') {
    activeChatId.value = sessions.value[0].chatId
  }
}

async function loadHistory(chatId) {
  const userId = readUserId()
  const trimmedAgentId = agentId.value.trim()
  const trimmedChatId = chatId.trim()
  if (userId === '' || trimmedAgentId === '' || trimmedChatId === '') {
    conversation.value = []
    return
  }

  const payload = await requestControlPlane(
    `/api/control-plane/chat/history/messages?agent_id=${encodeURIComponent(trimmedAgentId)}&chat_id=${encodeURIComponent(trimmedChatId)}&user_id=${encodeURIComponent(userId)}&limit=200`,
    { method: 'GET' }
  )
  const messages = Array.isArray(payload?.messages) ? payload.messages : []

  conversation.value = messages.map((item) => {
    return {
      id: uuid(),
      role: item?.role || item?.Role || 'assistant',
      text: parseContentText(item?.content || item?.Content),
      messageId: item?.message_id || item?.messageId || item?.MessageID || uuid(),
      createdAt: item?.created_at || item?.createdAt || item?.CreatedAt || new Date().toISOString()
    }
  })

  if (conversation.value.length === 0) {
    pushConversationMessage('assistant', 'New chat session ready. Describe the HR scenario to draft a response.')
  }
}

async function selectSession(chatId) {
  activeChatId.value = chatId
  error.value = ''
  await loadHistory(chatId)
}

function createSession() {
  const chatId = uuid()
  activeChatId.value = chatId
  conversation.value = []
  pushConversationMessage('assistant', 'New chat session ready. Describe the HR scenario to draft a response.')
  upsertSession(chatId, 'New conversation', new Date().toISOString())
}

async function persistHistoryMessage({ chatId, messageId, role, text, runId }) {
  const userId = readUserId()
  await requestControlPlane('/api/control-plane/chat/history/messages', {
    method: 'POST',
    body: {
      agent_id: agentId.value.trim(),
      chat_id: chatId,
      message_id: messageId,
      user_id: userId,
      role,
      content: {
        text,
        message: text,
        run_id: runId,
        chat_id: chatId,
        agent_id: agentId.value.trim(),
        organization_id: readOrgId()
      },
      metadata: {
        run_id: runId,
        chat_id: chatId,
        agent_id: agentId.value.trim(),
        organization_id: readOrgId(),
        role
      }
    }
  })
}

async function patchHistoryMessage(messageId, { chatId, runId, role, text }) {
  const userId = readUserId()
  await requestControlPlane(`/api/control-plane/chat/history/messages/${encodeURIComponent(messageId)}`, {
    method: 'PATCH',
    body: {
      agent_id: agentId.value.trim(),
      chat_id: chatId,
      user_id: userId,
      content: {
        text,
        message: text,
        run_id: runId,
        chat_id: chatId,
        agent_id: agentId.value.trim(),
        organization_id: readOrgId()
      },
      metadata: {
        run_id: runId,
        chat_id: chatId,
        role,
        invoke_status: 'completed'
      }
    }
  })
}

function buildInvokeMessages(nextUserText) {
  const historyTurns = conversation.value
    .filter((item) => item.role === 'user' || item.role === 'assistant')
    .map((item) => ({ role: item.role, content: item.text }))
  historyTurns.push({ role: 'user', content: nextUserText })
  return historyTurns.slice(-20)
}

async function sendMessage() {
  const text = draft.value.trim()
  if (text === '' || requestBusy.value) {
    return
  }

  if (registrationGate.state !== 'ready') {
    error.value = registrationGate.message
    return
  }

  const chatId = activeChatId.value.trim()
  if (chatId === '') {
    error.value = 'Create or select a chat session first.'
    return
  }

  const runId = uuid()
  const userMessageId = uuid()
  const assistantMessageId = uuid()
  const now = Date.now()

  error.value = ''
  requestBusy.value = true
  draft.value = ''
  pushConversationMessage('user', text, userMessageId)

  try {
    await persistHistoryMessage({ chatId, messageId: userMessageId, role: 'user', text, runId })

    const invokePayload = {
      schema_version: 'v1',
      run_id: runId,
      agent_id: agentId.value.trim(),
      agent_version: agentVersion.value.trim(),
      mode: 'realtime',
      trace: {
        trace_id: `trace-${now}`,
        span_id: `span-${now}`,
        tenant_id: readOrgId()
      },
      input: {
        message: text,
        chat_id: chatId,
        messages: buildInvokeMessages(text)
      },
      deadline_ms: 0,
      idempotency_key: `invoke-${runId}`
    }

    const invokeResult = await requestControlPlane('/api/control-plane/runtime/invoke', {
      method: 'POST',
      body: invokePayload
    })

    const reply = parseReply(invokeResult)
    pushConversationMessage('assistant', reply, assistantMessageId)

    await persistHistoryMessage({
      chatId,
      messageId: assistantMessageId,
      role: 'assistant',
      text: reply,
      runId
    })
    await patchHistoryMessage(userMessageId, {
      chatId,
      runId,
      role: 'user',
      text
    })

    upsertSession(chatId, text.slice(0, 72), new Date().toISOString())
  } catch (requestError) {
    const message = requestError instanceof Error ? requestError.message : 'Chat request failed.'
    error.value = message
    pushConversationMessage('assistant', `I could not complete that request.\n\n${message}`)
  } finally {
    requestBusy.value = false
  }
}

async function signIn() {
  if (authBusy.value) {
    return
  }
  authBusy.value = true
  authState.value = 'signing-in'
  authNotice.value = 'Signing in...'
  error.value = ''

  try {
    const payload = await requestControlPlane('/api/control-plane/auth/sessions', {
      method: 'POST',
      requiresAuth: false,
      body: {
        email: email.value.trim(),
        password: password.value
      }
    })

    const token =
      payload?.access_token ||
      payload?.accessToken ||
      payload?.token ||
      ''
    if (typeof token !== 'string' || token.trim() === '') {
      throw new Error('Sign-in succeeded but no access token returned.')
    }

    accessToken.value = token.trim()
    storeAccessToken(accessToken.value)
    await fetchMe()
    await fetchAvailableAgents()
    await preflightRegistration()
    await loadSessions()
    if (activeChatId.value.trim() !== '') {
      await loadHistory(activeChatId.value)
    } else {
      createSession()
    }
    authState.value = 'signed-in'
    authNotice.value = 'Signed in. Control-plane chat is ready.'
  } catch (requestError) {
    const message = requestError instanceof Error ? requestError.message : 'Sign-in failed.'
    clearLocalSession(message)
    error.value = message
  } finally {
    authBusy.value = false
  }
}

async function signOut() {
  if (authBusy.value) {
    return
  }
  authBusy.value = true
  try {
    await requestControlPlane('/api/control-plane/auth/sessions/current', { method: 'DELETE' })
  } catch (_error) {
    // Ignore logout request failures and clear local state anyway.
  } finally {
    clearLocalSession('Signed out.')
    authBusy.value = false
  }
}

watch(selectedAgentKey, async () => {
  applySelectedAgentKey()
  if (authState.value !== 'signed-in') {
    return
  }
  await preflightRegistration()
  await loadSessions()
  if (activeChatId.value.trim() !== '') {
    await loadHistory(activeChatId.value)
  }
})

watch([agentId, agentVersion], async () => {
  if (authState.value === 'signed-in') {
    await preflightRegistration()
    await loadSessions()
    if (activeChatId.value.trim() !== '') {
      await loadHistory(activeChatId.value)
    }
  }
})

onMounted(async () => {
  initializeTheme()
  const cachedToken = readAccessToken()
  accessToken.value = cachedToken
  if (accessToken.value === '') {
    clearLocalSession('Sign in to use control-plane invoke and chat history.')
    return
  }

  authState.value = 'signing-in'
  authNotice.value = 'Restoring session...'
  try {
    await fetchMe()
    await fetchAvailableAgents()
    authState.value = 'signed-in'
    authNotice.value = 'Session restored.'
    await preflightRegistration()
    await loadSessions()
    if (activeChatId.value.trim() !== '') {
      await loadHistory(activeChatId.value)
    } else {
      createSession()
    }
  } catch (_error) {
    clearLocalSession('Stored session is invalid. Sign in again.')
  }
})
</script>

<template>
  <main class="shell">
    <header class="topbar">
      <div class="brand-block">
        <img class="brand-logo" :src="activeLogo" alt="CraftsmanLabs logo" />
        <div>
          <p class="eyebrow">SimpleFlow HR Agent System</p>
          <h1>Control-Plane Chat</h1>
        </div>
      </div>
      <div class="topbar-actions">
        <button type="button" class="btn ghost" @click="toggleTheme">
          {{ currentTheme === 'dark' ? 'Light Mode' : 'Dark Mode' }}
        </button>
        <button type="button" class="btn ghost" @click="showConfig = !showConfig">
          {{ showConfig ? 'Hide Settings' : 'Settings' }}
        </button>
      </div>
    </header>

    <section class="status-row">
      <article class="pill" :class="`pill-${authState}`">
        <strong>Session:</strong> {{ authState }}
      </article>
      <article class="pill" :class="`pill-${registrationGate.state}`">
        <strong>Onboarding:</strong> {{ registrationGate.state }}
      </article>
      <article class="notice">{{ authNotice }}</article>
    </section>

    <section v-if="showConfig" class="panel config-panel">
      <label>
        Template Backend Base URL
        <input v-model="templateBackendBaseUrl" type="text" />
      </label>
      <button type="button" class="btn" @click="recheckRegistration">
        Refresh Onboarding
      </button>
    </section>

    <section v-if="authState !== 'signed-in'" class="panel auth-panel">
      <h2>Sign in required</h2>
      <p>Chat is disabled until you authenticate and an active runtime registration is found.</p>
      <div class="auth-grid">
        <label>
          Email
          <input v-model="email" type="email" autocomplete="username" />
        </label>
        <label>
          Password
          <input v-model="password" type="password" autocomplete="current-password" />
        </label>
      </div>
      <div class="auth-actions">
        <button type="button" class="btn" :disabled="authBusy" @click="signIn">
          {{ authBusy ? 'Signing in...' : 'Sign In' }}
        </button>
      </div>
    </section>

    <section v-else class="layout">
      <aside class="panel sessions-panel">
        <label>
          Agent
          <select v-model="selectedAgentKey">
            <option v-for="item in availableAgents" :key="agentOptionKey(item)" :value="agentOptionKey(item)">
              {{ item.agent_id }} @ {{ item.agent_version }}
            </option>
          </select>
        </label>
        <div class="sessions-head">
          <h2>Sessions</h2>
          <button type="button" class="btn" @click="createSession">New</button>
        </div>
        <p class="help">Each session uses one stable <code>chat_id</code> for all turns.</p>
        <div class="sessions-list">
          <button
            v-for="item in sortedSessions"
            :key="item.chatId"
            type="button"
            class="session-item"
            :class="{ active: item.chatId === activeChatId }"
            @click="selectSession(item.chatId)"
          >
            <strong>{{ item.title || 'Untitled chat' }}</strong>
            <span>{{ item.chatId }}</span>
          </button>
        </div>
      </aside>

      <section class="panel chat-panel">
        <header class="chat-panel-head">
          <div>
            <h2>HR Chat</h2>
            <p class="help">Chat invoke goes through <code>POST /api/control-plane/runtime/invoke</code> on template backend.</p>
          </div>
          <button type="button" class="btn ghost" @click="signOut">Sign Out</button>
        </header>

        <div class="onboarding-actions">
          <p class="gate-message">{{ registrationGate.message }}</p>
          <button v-if="registrationGate.state === 'missing'" type="button" class="btn" :disabled="onboardingBusy" @click="startOnboarding">
            {{ onboardingBusy ? 'Starting...' : 'Start Onboarding' }}
          </button>
          <button v-if="registrationGate.state === 'error' || registrationGate.state === 'unauthorized'" type="button" class="btn" :disabled="onboardingBusy" @click="retryOnboarding">
            {{ onboardingBusy ? 'Retrying...' : 'Retry Onboarding' }}
          </button>
        </div>

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
            placeholder="Describe the HR issue and ask for a warning email draft..."
            :disabled="registrationGate.state !== 'ready' || requestBusy"
            @keydown.enter.exact.prevent="sendMessage"
          />
          <div class="composer-actions">
            <p v-if="error !== ''" class="error">{{ error }}</p>
            <button type="button" class="btn" :disabled="!canSend" @click="sendMessage">
              {{ requestBusy ? 'Sending...' : 'Send' }}
            </button>
          </div>
        </footer>
      </section>
    </section>
  </main>
</template>

<style scoped>
:global(:root) {
  color-scheme: light;
}

:global(:root[data-theme='dark']) {
  color-scheme: dark;
}

:global(body) {
  margin: 0;
  font-family: 'IBM Plex Sans', 'Segoe UI', sans-serif;
  background:
    radial-gradient(1200px 560px at 8% 0%, rgba(226, 126, 90, 0.24), transparent 60%),
    radial-gradient(760px 540px at 90% 8%, rgba(91, 118, 160, 0.22), transparent 55%),
    var(--bg);
  color: var(--text);
}

:global(:root[data-theme='dark'] body) {
  --bg: #141a23;
  --panel: rgba(20, 29, 41, 0.9);
  --panel-soft: rgba(27, 38, 54, 0.88);
  --line: rgba(221, 226, 236, 0.17);
  --text: #f2f4f8;
  --muted: #b5becc;
  --accent: #d87a4f;
  --accent-ghost: rgba(216, 122, 79, 0.18);
  --danger: #f49b86;
}

:global(:root[data-theme='light'] body) {
  --bg: #f4efe7;
  --panel: rgba(255, 253, 250, 0.92);
  --panel-soft: rgba(250, 246, 239, 0.88);
  --line: rgba(30, 45, 68, 0.16);
  --text: #1f2d42;
  --muted: #5e6a7b;
  --accent: #b55f35;
  --accent-ghost: rgba(181, 95, 53, 0.13);
  --danger: #b13838;
}

.shell {
  width: min(1180px, calc(100vw - 2rem));
  margin: 1rem auto;
  display: grid;
  gap: 0.9rem;
}

.panel {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--panel);
  backdrop-filter: blur(4px);
}

.topbar {
  border: 1px solid var(--line);
  border-radius: 16px;
  background: color-mix(in srgb, var(--panel) 88%, transparent);
  padding: 0.9rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.brand-block {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.brand-logo {
  width: 44px;
  height: 44px;
}

.eyebrow {
  margin: 0;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: var(--muted);
}

h1,
h2 {
  margin: 0;
}

h1 {
  font-size: 1.35rem;
}

.topbar-actions {
  display: flex;
  gap: 0.5rem;
}

.status-row {
  display: grid;
  gap: 0.6rem;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.pill,
.notice {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 0.55rem 0.7rem;
  background: var(--panel-soft);
  font-size: 0.85rem;
}

.pill-ready,
.pill-signed-in {
  border-color: color-mix(in srgb, var(--line) 45%, #3aa26f 55%);
}

.pill-missing,
.pill-inactive,
.pill-error,
.pill-unauthorized {
  border-color: color-mix(in srgb, var(--line) 45%, var(--danger) 55%);
}

.config-panel,
.auth-panel {
  padding: 0.9rem;
  display: grid;
  gap: 0.7rem;
}

.auth-grid,
.config-panel {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

label {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
  font-weight: 600;
}

input,
select,
textarea {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0.6rem 0.65rem;
  font: inherit;
  background: color-mix(in srgb, var(--panel) 92%, transparent);
  color: var(--text);
}

.onboarding-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.6rem;
}

.layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 0.8rem;
}

.sessions-panel,
.chat-panel {
  padding: 0.8rem;
}

.sessions-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.help,
.gate-message {
  margin: 0.35rem 0;
  color: var(--muted);
  font-size: 0.82rem;
}

.sessions-list {
  display: grid;
  gap: 0.5rem;
  max-height: 62vh;
  overflow: auto;
  margin-top: 0.65rem;
}

.session-item {
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel-soft);
  color: var(--text);
  text-align: left;
  padding: 0.55rem;
  display: grid;
  gap: 0.2rem;
  cursor: pointer;
}

.session-item span {
  color: var(--muted);
  font-size: 0.72rem;
}

.session-item.active {
  border-color: var(--accent);
  background: var(--accent-ghost);
}

.chat-panel {
  display: grid;
  gap: 0.65rem;
}

.chat-panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.6rem;
}

.messages {
  min-height: 48vh;
  max-height: 60vh;
  overflow: auto;
  display: grid;
  gap: 0.65rem;
}

.bubble {
  max-width: 84%;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0.65rem;
  background: color-mix(in srgb, var(--panel-soft) 85%, transparent);
}

.bubble.user {
  margin-left: auto;
  background: color-mix(in srgb, var(--accent-ghost) 75%, var(--panel-soft) 25%);
}

.bubble.assistant {
  margin-right: auto;
}

pre {
  margin: 0.35rem 0 0;
  white-space: pre-wrap;
  font-family: 'IBM Plex Mono', 'Consolas', monospace;
  font-size: 0.82rem;
}

.composer {
  display: grid;
  gap: 0.5rem;
}

.composer-actions,
.auth-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.btn {
  border: 1px solid var(--accent);
  border-radius: 999px;
  padding: 0.5rem 0.9rem;
  font: inherit;
  font-weight: 600;
  color: #fff;
  background: var(--accent);
  cursor: pointer;
}

.btn.ghost {
  color: var(--text);
  border-color: var(--line);
  background: transparent;
}

.btn:disabled {
  opacity: 0.62;
  cursor: not-allowed;
}

.error {
  margin: 0;
  color: var(--danger);
  font-weight: 600;
  font-size: 0.86rem;
}

@media (max-width: 960px) {
  .status-row,
  .auth-grid,
  .config-panel {
    grid-template-columns: minmax(0, 1fr);
  }

  .layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .messages {
    max-height: 52vh;
  }

  .bubble {
    max-width: 96%;
  }
}
</style>
