import type { Product } from '../types'

interface Props {
  products: Product[]
  onAddToCart: (product: Product) => void
  addingId: string | null
  addedIds: Set<string>
}

export default function ProductGrid({ products, onAddToCart, addingId, addedIds }: Props) {
  if (!products.length) return null

  return (
    <div style={{ marginTop: '12px' }}>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
        gap: '12px',
      }}>
        {products.map(p => (
          <div key={p.product_id} style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: '12px',
            overflow: 'hidden',
            boxShadow: 'var(--shadow)',
            display: 'flex',
            flexDirection: 'column',
          }}>
            {p.image_url && (
              <img
                src={p.image_url}
                alt={p.title}
                style={{ width: '100%', height: '130px', objectFit: 'cover' }}
                onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
              />
            )}
            <div style={{ padding: '10px 12px', flex: 1, display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <div style={{ fontSize: '13px', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>
                {p.title}
              </div>
              <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--primary)' }}>
                ${p.price.toLocaleString('en-US', { minimumFractionDigits: 2 })}
              </div>
              <div style={{
                fontSize: '11px',
                color: p.in_stock ? 'var(--success)' : '#dc2626',
                fontWeight: 500,
              }}>
                {p.in_stock ? '● In stock' : '○ Out of stock'}
              </div>
              <button
                onClick={() => onAddToCart(p)}
                disabled={!p.in_stock || addingId === p.product_id}
                style={{
                  marginTop: 'auto',
                  padding: '7px 0',
                  borderRadius: '8px',
                  fontSize: '12px',
                  fontWeight: 600,
                  background: addedIds.has(p.product_id) ? '#dcfce7' : 'var(--primary)',
                  color: addedIds.has(p.product_id) ? 'var(--success)' : '#fff',
                  border: addedIds.has(p.product_id) ? '1px solid #86efac' : 'none',
                  opacity: (!p.in_stock || addingId === p.product_id) ? 0.6 : 1,
                  transition: 'all .2s',
                }}
              >
                {addingId === p.product_id
                  ? 'Adding…'
                  : addedIds.has(p.product_id)
                    ? 'Added ✓'
                    : 'Add to cart'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
