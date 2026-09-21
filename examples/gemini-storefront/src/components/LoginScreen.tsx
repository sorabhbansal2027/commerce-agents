import { useState, useRef } from 'react'

interface Props {
  onLogin: () => void
}

export default function LoginScreen({ onLogin }: Props) {
  const userRef = useRef<HTMLInputElement>(null)
  const passRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const username = userRef.current?.value.trim() ?? ''
    const password = passRef.current?.value ?? ''
    if (!username || !password) {
      setError('Please enter your username and password.')
      return
    }
    setError('')
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
      } else {
        setError(data.error ?? 'Invalid credentials.')
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
        maxWidth: '400px',
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
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            marginTop: '8px',
            background: '#312e81',
            color: '#a5b4fc',
            borderRadius: '999px',
            padding: '3px 12px',
            fontSize: '12px',
            fontWeight: 600,
          }}>
            <span style={{ color: '#86efac' }}>●</span> UCP · gemini-3.6-flash
          </div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={{ padding: '32px' }}>
          <div style={{ marginBottom: '8px', fontSize: '20px', fontWeight: 700, color: '#1a202c' }}>
            Sign in
          </div>
          <div style={{ fontSize: '13px', color: '#64748b', marginBottom: '24px' }}>
            Enter your credentials to continue
          </div>

          <label style={{ display: 'block', marginBottom: '16px' }}>
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#374151', display: 'block', marginBottom: '6px' }}>
              Username
            </span>
            <input
              ref={userRef}
              type="text"
              autoComplete="username"
              autoFocus
              placeholder="Enter username"
              style={{
                width: '100%',
                padding: '10px 14px',
                borderRadius: '10px',
                border: '1.5px solid #e2e8f0',
                fontSize: '14px',
                outline: 'none',
                transition: 'border-color .15s',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = '#1a56db' }}
              onBlur={e => { e.currentTarget.style.borderColor = '#e2e8f0' }}
            />
          </label>

          <label style={{ display: 'block', marginBottom: '24px' }}>
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#374151', display: 'block', marginBottom: '6px' }}>
              Password
            </span>
            <input
              ref={passRef}
              type="password"
              autoComplete="current-password"
              placeholder="Enter password"
              style={{
                width: '100%',
                padding: '10px 14px',
                borderRadius: '10px',
                border: '1.5px solid #e2e8f0',
                fontSize: '14px',
                outline: 'none',
                transition: 'border-color .15s',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = '#1a56db' }}
              onBlur={e => { e.currentTarget.style.borderColor = '#e2e8f0' }}
            />
          </label>

          {error && (
            <div style={{
              background: '#fef2f2',
              border: '1px solid #fecaca',
              borderRadius: '8px',
              padding: '10px 14px',
              fontSize: '13px',
              color: '#dc2626',
              marginBottom: '16px',
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: '12px',
              borderRadius: '10px',
              background: loading ? '#93c5fd' : '#1a56db',
              color: '#fff',
              fontWeight: 700,
              fontSize: '15px',
              transition: 'background .2s',
            }}
            onMouseOver={e => { if (!loading) e.currentTarget.style.background = '#1440a8' }}
            onMouseOut={e => { if (!loading) e.currentTarget.style.background = '#1a56db' }}
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>

          <div style={{ marginTop: '20px', padding: '12px', background: '#f8fafc', borderRadius: '8px', fontSize: '12px', color: '#64748b', textAlign: 'center' }}>
            Demo credentials: <strong>demo</strong> / <strong>demo123</strong>
          </div>
        </form>
      </div>
    </div>
  )
}
