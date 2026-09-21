import type { Quote } from '../types'

interface Props {
  quote: Quote
}

export default function QuoteConfirmation({ quote }: Props) {
  return (
    <div style={{
      border: '1px solid #e2e8f0',
      borderRadius: '12px',
      overflow: 'hidden',
      marginTop: '10px',
      background: '#fff',
      boxShadow: '0 2px 8px rgba(0,0,0,.06)',
    }}>
      {/* Header */}
      <div style={{
        background: '#0f172a',
        padding: '12px 16px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '16px' }}>📋</span>
          <span style={{ color: '#fff', fontWeight: 700, fontSize: '14px' }}>Quote Created</span>
        </div>
        <div style={{
          background: '#166534',
          color: '#bbf7d0',
          borderRadius: '6px',
          padding: '2px 8px',
          fontSize: '11px',
          fontWeight: 600,
        }}>
          DRAFT
        </div>
      </div>

      {/* Quote name */}
      <div style={{ padding: '10px 16px 4px', fontSize: '13px', fontWeight: 600, color: '#475569' }}>
        {quote.name}
      </div>

      {/* Line items */}
      <div style={{ padding: '0 16px' }}>
        {quote.items.map((item, idx) => (
          <div key={idx} style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '8px 0',
            borderBottom: idx < quote.items.length - 1 ? '1px solid #f1f5f9' : undefined,
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: '13px', fontWeight: 600, color: '#1e293b' }}>{item.title}</div>
              <div style={{ fontSize: '12px', color: '#64748b', marginTop: '2px' }}>
                {item.quantity} × ${item.price.toLocaleString('en-US', { minimumFractionDigits: 2 })}
              </div>
            </div>
            <div style={{ fontSize: '14px', fontWeight: 700, color: '#1e293b', marginLeft: '12px' }}>
              ${(item.price * item.quantity).toLocaleString('en-US', { minimumFractionDigits: 2 })}
            </div>
          </div>
        ))}
      </div>

      {/* Total */}
      <div style={{
        padding: '10px 16px',
        borderTop: '2px solid #e2e8f0',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        background: '#f8fafc',
      }}>
        <span style={{ fontWeight: 700, fontSize: '14px' }}>Total</span>
        <span style={{ fontWeight: 800, fontSize: '16px', color: '#1a56db' }}>
          ${quote.total.toLocaleString('en-US', { minimumFractionDigits: 2 })} {quote.currency}
        </span>
      </div>

      {/* Quote ID — user can copy this to load the quote later */}
      <div style={{
        padding: '7px 16px',
        background: '#f1f5f9',
        borderTop: '1px solid #e2e8f0',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <span style={{ fontSize: '11px', color: '#64748b' }}>Quote ID</span>
        <span style={{ fontSize: '12px', fontWeight: 700, color: '#334155', fontFamily: 'monospace' }}>
          {quote.quote_id}
        </span>
      </div>
    </div>
  )
}
