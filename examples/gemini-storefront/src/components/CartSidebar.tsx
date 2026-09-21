import type { CartItem } from '../types'

interface Props {
  items: CartItem[]
  onCheckout: () => void
  checkingOut: boolean
}

export default function CartSidebar({ items, onCheckout, checkingOut }: Props) {
  const total = items.reduce((s, i) => s + i.product.price * i.quantity, 0)

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

      {/* Footer */}
      {items.length > 0 && (
        <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px', fontWeight: 700 }}>
            <span>Total</span>
            <span style={{ color: 'var(--primary)' }}>
              ${total.toLocaleString('en-US', { minimumFractionDigits: 2 })}
            </span>
          </div>
          <button
            onClick={onCheckout}
            disabled={checkingOut}
            style={{
              width: '100%',
              padding: '10px',
              background: checkingOut ? '#93c5fd' : 'var(--primary)',
              color: '#fff',
              borderRadius: '10px',
              fontWeight: 700,
              fontSize: '14px',
              transition: 'background .2s',
            }}
          >
            {checkingOut ? 'Processing…' : 'Checkout with Gemini →'}
          </button>
        </div>
      )}
    </aside>
  )
}
