const tokenKey = 'simpleflow-hr-access-token'
const sharedControlPlaneTokenKey = 'simpleflow-access-token'

export function readAccessToken() {
  const localToken = window.localStorage.getItem(tokenKey) || ''
  if (localToken.trim() !== '') {
    return localToken
  }
  return window.localStorage.getItem(sharedControlPlaneTokenKey) || ''
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
