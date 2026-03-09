const tokenKey = 'simpleflow-hr-access-token'

export function readAccessToken() {
  return window.localStorage.getItem(tokenKey) || ''
}

export function storeAccessToken(token) {
  const normalized = typeof token === 'string' ? token.trim() : ''
  if (normalized === '') {
    return
  }
  window.localStorage.setItem(tokenKey, normalized)
}

export function clearAccessToken() {
  window.localStorage.removeItem(tokenKey)
}
