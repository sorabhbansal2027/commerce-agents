import { useState, useRef, useEffect } from 'react'

interface Props {
  onLogin: () => void
}

export default function LoginScreen({ onLogin }: Props) {
  const userRef = useRef<HTMLInputElement>(null)
  const passRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState('')
  const [needsToken, setNeedsToken] = useState(false)
  const [loading, setLoading] = useState(false)
  const [authMode, setAuthMode] = useState<'salesforce' | 'demo' | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then(r => r.json())
      .then(d => setAuthMode(d.auth_mode ?? 'demo'))
      .catch(() => setAuthMode('demo'))
  }, [])

  const isSalesforce = authMode === 'salesforce'

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const username = userRef.current?.value.trim() ?? ''
    const password = passRef.current?.value ?? ''
    if (!username || !password) {
      setError('Please enter your username and password.')
      return
    }
    setError('')
    setNeedsToken(false)
    setLoading(true)
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json()
      if (data.ok) {
        sessionStorage.setItem('gc_authed', '1')
        onLogin()
      } else if (data.error === 'SECURITY_TOKEN_REQUIRED') {
        setNeedsToken(true)
        setError('Append your Salesforce security token to your password and try again.')
      } else {
        setError(data.error ?? 'Authentication failed.')
      }
    } catch {
      setError('Cannot reach the server. Make sure the backend is running.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      height: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 60%, #0f172a 100%)',
    }}>
      <div style={{
        width: '100%',
        maxWidth: '420px',
        margin: '0 16px',
        background: '#fff',
        borderRadius: '20px',
        boxShadow: '0 25px 60px rgba(0,0,0,.4)',
        overflow: 'hidden',
      }}>
        {/* Brand header */}
        <div style={{
          background: '#0f172a',
          padding: '32px 32px 28px',
          textAlign: 'center',
        }}>
          <div style={{ fontSize: '40px', marginBottom: '10px' }}>🛍️</div>
          <div style={{ color: '#fff', fontWeight: 800, fontSize: '22px', letterSpacing: '-.3px' }}>
            Gemini Commerce
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', marginTop: '10px' }}>
            <span style={{
              background: '#312e81', color: '#a5b4fc',
              borderRadius: '999px', padding: '3px 12px',
              fontSize: '12px', fontWeight: 600,
              display: 'inline-flex', alignItems: 'center', gap: '5px',
            }}>
              <span style={{ color: '#86efac' }}>●</span> UCP · gemini-3.6-flash
            </span>
            {isSalesforce && (
              <span style={{
                background: '#00396b', color: '#7dd3fc',
                borderRadius: '999px', padding: '3px 12px',
                fontSize: '12px', fontWeight: 600,
              }}>
                Salesforce Auth
              </span>
            )}
          </div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={{ padding: '32px' }}>
          <div style={{ marginBottom: '6px', fontSize: '20px', fontWeight: 700, color: '#1a202c' }}>
            Sign in
          </div>
          <div style={{ fontSize: '13px', color: '#64748b', marginBottom: '24px' }}>
            {isSalesforce
              ? 'Use your Salesforce credentials to continue'
              : 'Enter your credentials to continue'}
          </div>

          <label style={{ display: 'block', marginBottom: '16px' }}>
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#374151', display: 'block', marginBottom: '6px' }}>
              {isSalesforce ? 'Salesforce Username' : 'Username'}
            </span>
            <input
              ref={userRef}
              type={isSalesforce ? 'email' : 'text'}
              autoComplete="username"
              autoFocus
              placeholder={isSalesforce ? 'you@example.com' : 'Enter username'}
              style={{
                width: '100%', padding: '10px 14px',
                borderRadius: '10px', border: '1.5px solid #e2e8f0',
                fontSize: '14px', outline: 'none', transition: 'border-color .15s',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = '#1a56db' }}
              onBlur={e => { e.currentTarget.style.borderColor = '#e2e8f0' }}
            />
          </label>

          <label style={{ display: 'block', marginBottom: isSalesforce ? '8px' : '24px' }}>
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#374151', display: 'block', marginBottom: '6px' }}>
              Password
            </span>
            <input
              ref={passRef}
              type="password"
              autoComplete="current-password"
              placeholder="Enter password"
              style={{
                width: '100%', padding: '10px 14px',
                borderRadius: '10px', border: '1.5px solid #e2e8f0',
                fontSize: '14px', outline: 'none', transition: 'border-color .15s',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = '#1a56db' }}
              onBlur={e => { e.currentTarget.style.borderColor = '#e2e8f0' }}
            />
          </label>

          {isSalesforce && (
            <div style={{
              marginBottom: '20px',
              padding: needsToken ? '10px 14px' : '0',
              borderRadius: needsToken ? '8px' : '0',
              background: needsToken ? '#fffbeb' : 'transparent',
              border: needsToken ? '1px solid #fcd34d' : 'none',
              fontSize: '12px',
              color: needsToken ? '#92400e' : '#94a3b8',
              transition: 'all .2s',
            }}>
              {needsToken && <div style={{ fontWeight: 700, marginBottom: '4px' }}>🔑 Security token required</div>}
              {needsToken
                ? 'Your network is not trusted by Salesforce. Append your security token directly to your password (e.g. MyPassword + AbCdEfGhIj123) and try again. Get your token: Salesforce Setup → Personal Settings → Reset My Security Token.'
                : 'If your IP is not trusted, append your security token to the password.'}
            </div>
          )}

          {error && (
            <div style={{
              background: '#fef2f2', border: '1px solid #fecaca',
              borderRadius: '8px', padding: '10px 14px',
              fontSize: '13px', color: '#dc2626', marginBottom: '16px',
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || authMode === null}
            style={{
              width: '100%', padding: '12px', borderRadius: '10px',
              background: loading || authMode === null ? '#93c5fd' : '#1a56db',
              color: '#fff', fontWeight: 700, fontSize: '15px', transition: 'background .2s',
            }}
            onMouseOver={e => { if (!loading && authMode !== null) e.currentTarget.style.background = '#1440a8' }}
            onMouseOut={e => { if (!loading && authMode !== null) e.currentTarget.style.background = '#1a56db' }}
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>

          {!isSalesforce && authMode === 'demo' && (
            <div style={{ marginTop: '20px', padding: '12px', background: '#f8fafc', borderRadius: '8px', fontSize: '12px', color: '#64748b', textAlign: 'center' }}>
              Demo credentials: <strong>demo</strong> / <strong>demo123</strong>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
