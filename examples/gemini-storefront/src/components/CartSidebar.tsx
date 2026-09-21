import { useState } from 'react'
import type { CartItem } from '../types'

interface Props {
  items: CartItem[]
  onCheckout: () => void
  onCheckoutPO: () => void
  onSaveQuote: () => void
  onLoadQuote: (quoteId: string) => void
  busy: boolean
}

export default function CartSidebar({ items, onCheckout, onCheckoutPO, onSaveQuote, onLoadQuote, busy }: Props) {
  const total = items.reduce((s, i) => s + i.product.price * i.quantity, 0)
  const [quoteInput, setQuoteInput] = useState('')

  return (
    <aside style={{
      width: '280px',
      minWidth: '280px',
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: '16px',
      boxShadow: 'var(--shadow-md)',
      display: 'flex',
      flexDirection: 'column',
      height: 'fit-content',
      position: 'sticky',
      top: '20px',
    }}>
      {/* Header */}
      <div style={{
        padding: '16px 18px',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
      }}>
        <span style={{ fontSize: '18px' }}>🛒</span>
        <span style={{ fontWeight: 700, fontSize: '15px' }}>Cart</span>
        {items.length > 0 && (
          <span style={{
            marginLeft: 'auto',
            background: 'var(--primary)',
            color: '#fff',
            borderRadius: '999px',
            padding: '1px 8px',
            fontSize: '12px',
            fontWeight: 600,
          }}>
            {items.reduce((s, i) => s + i.quantity, 0)}
          </span>
        )}
      </div>

      {/* Items */}
      <div style={{ padding: '12px', flex: 1, overflowY: 'auto', maxHeight: '360px' }}>
        {items.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '13px', textAlign: 'center', padding: '24px 0' }}>
            Your cart is empty
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {items.map(item => (
              <div key={item.product.product_id} style={{
                display: 'flex',
                gap: '10px',
                padding: '8px',
                background: '#f8fafc',
                borderRadius: '8px',
                alignItems: 'flex-start',
              }}>
                {item.product.image_url && (
                  <img
                    src={item.product.image_url}
                    alt={item.product.title}
                    style={{ width: '44px', height: '44px', objectFit: 'cover', borderRadius: '6px', flexShrink: 0 }}
                    onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                  />
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '12px', fontWeight: 600, lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {item.product.title}
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
                    Qty: {item.quantity}
                  </div>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--primary)', marginTop: '2px' }}>
                    ${(item.product.price * item.quantity).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Actions when cart has items */}
      {items.length > 0 && (
        <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {/* Total */}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: 700 }}>
            <span>Total</span>
            <span style={{ color: 'var(--primary)' }}>
              ${total.toLocaleString('en-US', { minimumFractionDigits: 2 })}
            </span>
          </div>

          {/* Save as Quote */}
          <button
            onClick={onSaveQuote}
            disabled={busy}
            style={{
              width: '100%', padding: '8px',
              background: busy ? '#f1f5f9' : '#f8fafc',
              color: busy ? '#94a3b8' : '#334155',
              borderRadius: '8px', fontWeight: 600, fontSize: '13px',
              border: '1px solid var(--border)',
              transition: 'background .15s',
              cursor: busy ? 'not-allowed' : 'pointer',
            }}
          >
            📋 Save as Quote
          </button>

          {/* Checkout — Credit Card */}
          <button
            onClick={onCheckout}
            disabled={busy}
            style={{
              width: '100%', padding: '10px',
              background: busy ? '#93c5fd' : 'var(--primary)',
              color: '#fff', borderRadius: '10px',
              fontWeight: 700, fontSize: '14px',
              transition: 'background .2s',
            }}
          >
            {busy ? 'Processing…' : 'Checkout (Credit Card) →'}
          </button>

          {/* Checkout — Purchase Order */}
          <button
            onClick={onCheckoutPO}
            disabled={busy}
            style={{
              width: '100%', padding: '10px',
              background: busy ? '#e2e8f0' : '#0f172a',
              color: busy ? '#94a3b8' : '#fff',
              borderRadius: '10px', fontWeight: 700, fontSize: '14px',
              transition: 'background .2s',
            }}
          >
            {busy ? 'Processing…' : 'Place Order (PO) →'}
          </button>
        </div>
      )}

      {/* Load Quote */}
      <div style={{
        padding: '12px 16px',
        borderTop: '1px solid var(--border)',
      }}>
        <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Load Quote
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          <input
            type="text"
            value={quoteInput}
            onChange={e => setQuoteInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && quoteInput.trim() && !busy) {
                onLoadQuote(quoteInput.trim())
                setQuoteInput('')
              }
            }}
            placeholder="Q-ABCD1234"
            disabled={busy}
            style={{
              flex: 1, padding: '7px 10px',
              borderRadius: '8px', border: '1.5px solid var(--border)',
              fontSize: '13px', outline: 'none', minWidth: 0,
              background: busy ? '#f8fafc' : '#fff',
            }}
            onFocus={e => { e.currentTarget.style.borderColor = 'var(--primary)' }}
            onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
          />
          <button
            onClick={() => {
              if (quoteInput.trim() && !busy) {
                onLoadQuote(quoteInput.trim())
                setQuoteInput('')
              }
            }}
            disabled={!quoteInput.trim() || busy}
            style={{
              padding: '7px 12px',
              background: !quoteInput.trim() || busy ? '#e2e8f0' : 'var(--primary)',
              color: !quoteInput.trim() || busy ? '#94a3b8' : '#fff',
              borderRadius: '8px', fontWeight: 700, fontSize: '13px',
              flexShrink: 0,
            }}
          >
            Load
          </button>
        </div>
      </div>
    </aside>
  )
}
