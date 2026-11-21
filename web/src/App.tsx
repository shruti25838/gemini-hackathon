import { useEffect, useState } from 'react'
import type { CatalogOffer, ContractName } from './types/contracts'
import { SourceBadge } from './components/SourceBadge'

/**
 * Phase 0 shell.
 *
 * It does one honest job: prove the frontend is wired to this backend, that
 * the generated contract types compile, and that every piece of data on
 * screen carries its source label. The 3D room, the activity panel and the
 * review card arrive in Phase 3.
 */

type Health = {
  status: string
  version: string
  mocks_on: string[]
  commerce: 'live' | 'mock'
  shop_mcp: string
  phase: string
}

const CONTRACTS: ContractName[] = [
  'ActivityEvent',
  'CartReview',
  'CatalogOffer',
  'CheckoutAudit',
  'DesignBrief',
  'FitReport',
  'PlacementReview',
  'RecoveryRun',
  'RoomSnapshot',
  'RoomSpec',
  'SceneRevision',
  'WorkflowPlan',
]

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((response) => {
        if (!response.ok) throw new Error(`backend returned ${response.status}`)
        return response.json()
      })
      .then(setHealth)
      .catch((cause: Error) => setError(cause.message))
  }, [])

  return (
    <main className="shell">
      <header>
        <h1>Ethyneon</h1>
        <p className="lede">
          A room photo becomes an editable 3D room, verified Shopify products go into it,
          the fit is checked, and the shopper is handed to a hosted test checkout.
        </p>
      </header>

      <section>
        <h2>Backend</h2>
        {error && (
          <p className="error">
            Cannot reach the backend: {error}. Start it with <code>scripts/dev</code>.
          </p>
        )}
        {!health && !error && <p className="muted">Checking…</p>}
        {health && (
          <dl className="facts">
            <dt>Status</dt>
            <dd>
              {health.status} · v{health.version}
            </dd>
            <dt>Phase</dt>
            <dd>{health.phase}</dd>
            <dt>Commerce</dt>
            <dd>
              <SourceBadge label={health.commerce === 'live' ? 'live' : 'mock'} />
              {health.commerce === 'live' ? ' development store' : ' local fixture catalog'}
            </dd>
            <dt>Shop MCP</dt>
            <dd>
              {health.shop_mcp} · discovery only, never shoppable
            </dd>
            <dt>Mocks on</dt>
            <dd>
              {health.mocks_on.length === 0 ? (
                <span className="muted">none</span>
              ) : (
                health.mocks_on.map((flag) => <code key={flag}>{flag}</code>)
              )}
            </dd>
          </dl>
        )}
      </section>

      <section>
        <h2>Contracts</h2>
        <p className="muted">
          {CONTRACTS.length} shared data formats, generated from the Pydantic models into{' '}
          <code>web/src/types/contracts.ts</code>. Never hand-edited.
        </p>
        <ul className="chips">
          {CONTRACTS.map((name) => (
            <li key={name}>
              <code>{name}</code>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>Source labels</h2>
        <p className="muted">
          Everything shown to the shopper carries its provenance. A mock never looks live.
        </p>
        <ul className="chips">
          {(['live', 'cached', 'preset', 'generated', 'inferred', 'mock'] as const).map(
            (label) => (
              <li key={label}>
                <SourceBadge label={label} />
              </li>
            ),
          )}
        </ul>
      </section>

      <footer>
        <p className="muted">
          Phase 0: scaffold and contracts. The 3D room, placement, activity panel and review
          card land in Phase 3.
        </p>
      </footer>
    </main>
  )
}

// Compile-time proof that the generated types are usable. If a contract field
// is renamed without regenerating, `npm run build` fails here.
export const EXAMPLE_OFFER_SHAPE: Pick<
  CatalogOffer,
  'offer_id' | 'product_id' | 'variant_id' | 'shoppable'
> = {
  offer_id: 'offer_dev_desk_walnut',
  product_id: 'gid://shopify/Product/9000000000001',
  variant_id: 'gid://shopify/ProductVariant/49000000000001',
  shoppable: true,
}
