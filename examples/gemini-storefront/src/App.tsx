import { useState, useCallback, useEffect } from 'react'
import ChatPanel from './components/ChatPanel'
import CartSidebar from './components/CartSidebar'
import LoginScreen from './components/LoginScreen'
import type { Message, Product, CartItem } from './types'

const API = '/api'   // proxied to localhost:8090 via vite.config.ts

let msgId = 0
const uid = () => String(++msgId)

export default function App() {
  const [authed, setAuthed] = useState(() => sessionStorage.getItem('gc_authed') === '1')
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const [cartItems, setCartItems] = useState<CartItem[]>([])
  const [addingId, setAddingId] = useState<string | null>(null)
  const [addedIds, setAddedIds] = useState<Set<string>>(new Set())

  // Clear cart whenever a placed-order message arrives, regardless of how sendMessage ran
  useEffect(() => {
    const last = messages[messages.length - 1]
    if (!last?.loading && last?.checkoutSession?.status === 'placed') {
      setCartItems(prev => prev.length > 0 ? [] : prev)
      setAddedIds(prev => prev.size > 0 ? new Set() : prev)
    }
  }, [messages])

  // On page load, verify the server session is still valid before trusting
  // the browser's cached auth state.  After a server redeploy the in-memory
  // buyer user ID is gone even though sessionStorage still says authed=1.
  useEffect(() => {
    if (!authed) return
    fetch(`${API}/session`)
      .then(r => r.json())
      .then(d => {
        if (!d.authenticated) {
          sessionStorage.removeItem('gc_authed')
          setAuthed(false)
        } else {
          loadActiveCart()
        }
      })
      .catch(() => loadActiveCart()) // network error — optimistically proceed
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const sendMessage = useCallback(async (text: string) => {
    if (loading) return
    setLoading(true)

    const userMsg: Message = { id: uid(), role: 'user', text }
    const pendingMsg: Message = { id: uid(), role: 'assistant', text: '', loading: true }
    setMessages(prev => [...prev, userMsg, pendingMsg])

    try {
      const res = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })
      const data = await res.json()

      setMessages(prev => prev.map(m =>
        m.id === pendingMsg.id
          ? {
              ...m,
              text: data.reply,
              products: data.products ?? [],
              toolCalls: data.tool_calls ?? [],
              checkoutSession: data.checkout_session ?? undefined,
              quoteResult: data.quote_result ?? undefined,
              loading: false,
            }
          : m
      ))

      // Clear cart when an order was placed this turn
      const orderPlaced =
        (data.tool_calls as Array<{tool: string}> ?? [])
          .some(tc => tc.tool === 'place_b2b_order' || tc.tool === 'place_order') ||
        data.checkout_session?.status === 'placed'
      if (orderPlaced) {
        setCartItems([])
        setAddedIds(new Set())
      }

      // Sync cart when Gemini added or loaded items via tool calls
      if (!orderPlaced && data.cart_additions?.length) {
        for (const { product, quantity } of data.cart_additions) {
          setCartItems(prev => {
            const existing = prev.find(i => i.product.product_id === product.product_id)
            if (existing) return prev.map(i => i.product.product_id === product.product_id ? { ...i, quantity: i.quantity + quantity } : i)
            return [...prev, { product, quantity }]
          })
          setAddedIds(prev => new Set([...prev, product.product_id]))
        }
      }
    } catch {
      setMessages(prev => prev.map(m =>
        m.id === pendingMsg.id
          ? { ...m, text: 'Could not reach the Gemini backend. Is `gemini_web_server.py` running on port 8090?', loading: false }
          : m
      ))
    } finally {
      setLoading(false)
    }
  }, [loading])

  const addToCart = useCallback(async (product: Product) => {
    if (addingId) return
    setAddingId(product.product_id)

    // Optimistic: add to local cart immediately
    setCartItems(prev => {
      const existing = prev.find(i => i.product.product_id === product.product_id)
      if (existing) return prev.map(i => i.product.product_id === product.product_id ? { ...i, quantity: i.quantity + 1 } : i)
      return [...prev, { product, quantity: 1 }]
    })
    setAddedIds(prev => new Set([...prev, product.product_id]))

    // Tell Gemini so it tracks the item for checkout / quote
    const pendingMsg: Message = { id: uid(), role: 'assistant', text: '', loading: true }
    setMessages(prev => [...prev, pendingMsg])
    try {
      const res = await fetch(`${API}/cart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: product.product_id, quantity: 1, product_name: product.title }),
      })
      const data = await res.json()
      setMessages(prev => prev.map(m =>
        m.id === pendingMsg.id
          ? { ...m, text: data.reply, toolCalls: data.tool_calls ?? [], loading: false }
          : m
      ))
    } catch {
      setMessages(prev => prev.filter(m => m.id !== pendingMsg.id))
    } finally {
      setAddingId(null)
    }
  }, [addingId])

  // All cart actions go through sendMessage so Gemini's tools handle them
  const checkout = useCallback(() => {
    if (cartItems.length === 0) return
    const names = cartItems.map(i => `${i.product.title} (qty ${i.quantity})`).join(', ')
    sendMessage(`Please place an order for: ${names}. Payment method: credit_card.`)
  }, [cartItems, sendMessage])

  const checkoutPO = useCallback((poNumber: string) => {
    if (cartItems.length === 0) return
    const names = cartItems.map(i => `${i.product.title} (qty ${i.quantity})`).join(', ')
    sendMessage(`Please place a purchase order for: ${names}. PO number: ${poNumber}. Payment method: purchase_order.`)
  }, [cartItems, sendMessage])

  const saveAsQuote = useCallback(() => {
    if (cartItems.length === 0) return
    const names = cartItems.map(i => `${i.product.title} (qty ${i.quantity})`).join(', ')
    sendMessage(`Save my current cart as a quote. Items: ${names}.`)
  }, [cartItems, sendMessage])

  const loadQuote = useCallback((quoteId: string) => {
    sendMessage(`Load quote ${quoteId} to my cart.`)
  }, [sendMessage])

  const loadActiveCart = useCallback(async () => {
    try {
      const res = await fetch(`${API}/active-cart`)
      const data = await res.json()
      if (data.items?.length) {
        const newItems: CartItem[] = data.items.map((it: {
          product_id: string; title: string; price: number;
          currency: string; image_url?: string; quantity: number
        }) => ({
          product: {
            product_id: it.product_id,
            title: it.title,
            price: it.price,
            currency: it.currency,
            in_stock: true,
            image_url: it.image_url,
          },
          quantity: it.quantity,
        }))
        setCartItems(newItems)
        setAddedIds(new Set(newItems.map(i => i.product.product_id)))
      }
    } catch {
      // silently ignore — cart stays empty if fetch fails
    }
  }, [])

  const resetConversation = useCallback(async () => {
    await fetch(`${API}/reset`, { method: 'POST' })
    setMessages([])
    setCartItems([])
    setAddedIds(new Set())
  }, [])

  const logout = useCallback(() => {
    sessionStorage.removeItem('gc_authed')
    setAuthed(false)
    setMessages([])
    setCartItems([])
    setAddedIds(new Set())
  }, [])

  if (!authed) return <LoginScreen onLogin={() => { setAuthed(true); loadActiveCart() }} />

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg)' }}>
      {/* Top nav */}
      <header style={{
        padding: '0 24px',
        height: '56px',
        background: '#0f172a',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexShrink: 0,
        boxShadow: '0 1px 0 rgba(255,255,255,.08)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '20px' }}>🛍️</span>
          <span style={{ color: '#fff', fontWeight: 700, fontSize: '16px' }}>Gemini Commerce</span>
          <span style={{
            background: '#312e81', color: '#a5b4fc',
            borderRadius: '6px', padding: '2px 8px', fontSize: '11px', fontWeight: 600,
          }}>
            UCP · gemini-3.6-flash
          </span>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            onClick={resetConversation}
            style={{
              background: 'transparent', color: '#94a3b8',
              fontSize: '13px', padding: '6px 12px',
              borderRadius: '8px', border: '1px solid #334155',
            }}
            onMouseOver={e => { e.currentTarget.style.color = '#fff'; e.currentTarget.style.borderColor = '#475569' }}
            onMouseOut={e => { e.currentTarget.style.color = '#94a3b8'; e.currentTarget.style.borderColor = '#334155' }}
          >
            New conversation
          </button>
          <button
            onClick={logout}
            style={{
              background: 'transparent', color: '#94a3b8',
              fontSize: '13px', padding: '6px 12px',
              borderRadius: '8px', border: '1px solid #334155',
            }}
            onMouseOver={e => { e.currentTarget.style.color = '#fff'; e.currentTarget.style.borderColor = '#475569' }}
            onMouseOut={e => { e.currentTarget.style.color = '#94a3b8'; e.currentTarget.style.borderColor = '#334155' }}
          >
            Sign out
          </button>
        </div>
      </header>

      {/* Main layout */}
      <main style={{
        flex: 1,
        overflow: 'hidden',
        display: 'flex',
        gap: '20px',
        padding: '20px 24px',
        maxWidth: '1200px',
        width: '100%',
        margin: '0 auto',
        alignItems: 'stretch',
      }}>
        <ChatPanel
          messages={messages}
          onSend={sendMessage}
          loading={loading}
          onAddToCart={addToCart}
          addingId={addingId}
          addedIds={addedIds}
        />
        <div style={{ alignSelf: 'flex-start', position: 'sticky', top: 0 }}>
          <CartSidebar
            items={cartItems}
            onCheckout={checkout}
            onCheckoutPO={checkoutPO}
            onSaveQuote={saveAsQuote}
            onLoadQuote={loadQuote}
            busy={loading}
          />
        </div>
      </main>
    </div>
  )
}
