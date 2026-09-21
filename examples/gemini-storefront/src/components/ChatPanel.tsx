import { useEffect, useRef } from 'react'
import type { Message } from '../types'
import ProductGrid from './ProductGrid'
import CheckoutConfirmation from './CheckoutConfirmation'
import type { Product } from '../types'

interface Props {
  messages: Message[]
  onSend: (text: string) => void
  loading: boolean
  onAddToCart: (product: Product) => void
  addingId: string | null
  addedIds: Set<string>
}

const SUGGESTIONS = [
  'Show me laptops under $2000',
  'What camping gear do you have?',
  'Find a gift under $100',
  'Compare your top 2 headphones',
]

export default function ChatPanel({ messages, onSend, loading, onAddToCart, addingId, addedIds }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const val = inputRef.current?.value.trim()
    if (!val || loading) return
    onSend(val)
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: '16px',
      boxShadow: 'var(--shadow-md)',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '14px 20px',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        background: '#fff',
      }}>
        <div style={{
          width: '32px', height: '32px',
          borderRadius: '50%',
          background: 'linear-gradient(135deg, #8b5cf6, #6366f1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#fff', fontSize: '15px', fontWeight: 700,
        }}>G</div>
        <div>
          <div style={{ fontWeight: 700, fontSize: '14px' }}>Gemini Shopping Assistant</div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
            <span style={{ color: '#16a34a', fontWeight: 600 }}>● </span>gemini-3.6-flash · UCP
          </div>
        </div>
      </div>

      {/* Messages */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '16px',
      }}>
        {messages.length === 0 && (
          <div style={{ margin: 'auto', textAlign: 'center', color: 'var(--text-muted)', paddingTop: '32px' }}>
            <div style={{ fontSize: '36px', marginBottom: '12px' }}>🛍️</div>
            <div style={{ fontWeight: 600, fontSize: '16px', color: 'var(--text)', marginBottom: '6px' }}>
              What are you shopping for?
            </div>
            <div style={{ fontSize: '13px', marginBottom: '24px' }}>
              Ask Gemini to search, compare, or build a cart
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' }}>
              {SUGGESTIONS.map(s => (
                <button
                  key={s}
                  onClick={() => onSend(s)}
                  style={{
                    padding: '7px 14px',
                    borderRadius: '999px',
                    border: '1px solid var(--border)',
                    background: 'var(--bg)',
                    fontSize: '13px',
                    color: 'var(--text)',
                    transition: 'all .15s',
                  }}
                  onMouseOver={e => { (e.currentTarget.style.background = '#e0e7ff'); (e.currentTarget.style.borderColor = '#a5b4fc') }}
                  onMouseOut={e => { (e.currentTarget.style.background = 'var(--bg)'); (e.currentTarget.style.borderColor = 'var(--border)') }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map(msg => (
          <div key={msg.id} style={{
            display: 'flex',
            flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
            alignItems: 'flex-start',
            gap: '8px',
          }}>
            {/* Avatar */}
            <div style={{
              width: '28px', height: '28px', borderRadius: '50%', flexShrink: 0,
              background: msg.role === 'user' ? '#dbeafe' : 'linear-gradient(135deg, #8b5cf6, #6366f1)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '13px', fontWeight: 700, color: msg.role === 'user' ? '#1a56db' : '#fff',
            }}>
              {msg.role === 'user' ? 'S' : 'G'}
            </div>

            {/* Bubble */}
            <div style={{ maxWidth: '80%' }}>
              {msg.role === 'user' ? (
                <div style={{
                  background: 'var(--user-bubble)', color: '#fff',
                  padding: '10px 14px', borderRadius: '16px 4px 16px 16px',
                  fontSize: '14px', lineHeight: 1.5,
                }}>
                  {msg.text}
                </div>
              ) : (
                <div style={{
                  background: 'var(--assistant-bubble)',
                  border: '1px solid var(--border)',
                  padding: '12px 14px', borderRadius: '4px 16px 16px 16px',
                  fontSize: '14px', lineHeight: 1.6,
                  boxShadow: 'var(--shadow)',
                }}>
                  {msg.loading ? (
                    <span style={{ color: 'var(--text-muted)' }}>
                      <span style={{ animation: 'pulse 1.5s ease-in-out infinite' }}>Gemini is thinking…</span>
                    </span>
                  ) : (
                    <>
                      <div style={{ whiteSpace: 'pre-wrap' }}>{msg.text}</div>
                      {msg.toolCalls && msg.toolCalls.length > 0 && (
                        <div style={{ marginTop: '8px', display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                          {msg.toolCalls.map((tc, i) => (
                            <span key={i} style={{
                              background: '#f0f4ff', border: '1px solid #c7d2fe',
                              borderRadius: '6px', padding: '2px 8px',
                              fontSize: '11px', color: '#4338ca', fontFamily: 'monospace',
                            }}>
                              → {tc.tool}
                            </span>
                          ))}
                        </div>
                      )}
                      {msg.products && msg.products.length > 0 && (
                        <ProductGrid
                          products={msg.products}
                          onAddToCart={onAddToCart}
                          addingId={addingId}
                          addedIds={addedIds}
                        />
                      )}
                      {msg.checkoutSession && (
                        <CheckoutConfirmation session={msg.checkoutSession} />
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} style={{
        padding: '14px 16px',
        borderTop: '1px solid var(--border)',
        display: 'flex',
        gap: '10px',
        background: '#fff',
      }}>
        <input
          ref={inputRef}
          placeholder="Ask Gemini to search, compare, or add to cart…"
          disabled={loading}
          style={{
            flex: 1,
            padding: '10px 14px',
            borderRadius: '10px',
            border: '1px solid var(--border)',
            fontSize: '14px',
            outline: 'none',
            background: loading ? '#f8fafc' : '#fff',
            transition: 'border-color .15s',
          }}
          onFocus={e => { e.currentTarget.style.borderColor = 'var(--primary)' }}
          onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
        />
        <button
          type="submit"
          disabled={loading}
          style={{
            padding: '10px 20px',
            borderRadius: '10px',
            background: loading ? '#93c5fd' : 'var(--primary)',
            color: '#fff',
            fontWeight: 600,
            fontSize: '14px',
            flexShrink: 0,
            transition: 'background .2s',
          }}
        >
          {loading ? '…' : 'Send'}
        </button>
      </form>

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
      `}</style>
    </div>
  )
}
