import type { CheckoutSession } from '../types'

interface Props {
  session: CheckoutSession
}

const PAYMENT_LABELS: Record<string, string> = {
  purchase_order: 'Purchase Order',
  credit_card: 'Credit Card',
}

export default function CheckoutConfirmation({ session }: Props) {
  const paymentLabel = PAYMENT_LABELS[session.payment_handler] ?? session.payment_handler
  const isPO = session.payment_handler === 'purchase_order'

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
          <span style={{ fontSize: '16px' }}>🧾</span>
          <span style={{ color: '#fff', fontWeight: 700, fontSize: '14px' }}>Order Confirmation</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{
            background: isPO ? '#334155' : '#1e3a8a',
            color: '#e0e7ff',
            borderRadius: '6px',
            padding: '2px 8px',
            fontSize: '11px',
            fontWeight: 600,
          }}>
            {paymentLabel}
          </span>
          <span style={{
            background: '#166534',
            color: '#bbf7d0',
            borderRadius: '6px',
            padding: '2px 8px',
            fontSize: '11px',
            fontWeight: 600,
          }}>
            {session.status.toUpperCase()}
          </span>
        </div>
      </div>

      {/* Line items */}
      <div style={{ padding: '0 16px' }}>
        {session.line_items.map((item, idx) => (
          <div key={idx} style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '10px 0',
            borderBottom: idx < session.line_items.length - 1 ? '1px solid #f1f5f9' : undefined,
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: '13px', fontWeight: 600, color: '#1e293b' }}>
                {item.title}
              </div>
              <div style={{ fontSize: '12px', color: '#64748b', marginTop: '2px' }}>
                {item.quantity} × ${item.unit_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}
              </div>
            </div>
            <div style={{ fontSize: '14px', fontWeight: 700, color: '#1e293b', marginLeft: '12px' }}>
              ${item.line_total.toLocaleString('en-US', { minimumFractionDigits: 2 })}
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
          ${session.subtotal.toLocaleString('en-US', { minimumFractionDigits: 2 })} {session.currency}
        </span>
      </div>

      {/* Buyer info if present */}
      {(session.buyer?.name || session.buyer?.email) && (
        <div style={{ padding: '8px 16px', borderTop: '1px solid #e2e8f0', fontSize: '12px', color: '#64748b' }}>
          {session.buyer.name && <span style={{ marginRight: '12px' }}>👤 {session.buyer.name}</span>}
          {session.buyer.email && <span>✉ {session.buyer.email}</span>}
        </div>
      )}

      {/* Session ID footer */}
      <div style={{
        padding: '6px 16px',
        background: '#f1f5f9',
        borderTop: '1px solid #e2e8f0',
        fontSize: '11px',
        color: '#94a3b8',
        fontFamily: 'monospace',
      }}>
        {session.checkout_session_id}
      </div>
    </div>
  )
}
