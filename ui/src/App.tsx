import { useEffect, useState } from 'react'
import { NavLink, Route, Routes, useParams } from 'react-router-dom'
import { api } from './api'
import type { Health } from './types'
import ClaimsList from './pages/ClaimsList'
import ClaimDetail from './pages/ClaimDetail'
import SubmitClaim from './pages/SubmitClaim'
import PolicyPage from './pages/PolicyPage'
import BenchmarkPage from './pages/BenchmarkPage'

function StoreClaim() {
  const { id = '' } = useParams()
  return <ClaimDetail source={{ kind: 'store', claimId: id }} />
}

function BenchmarkClaim() {
  const { run = '', claimId = '' } = useParams()
  return <ClaimDetail source={{ kind: 'benchmark', run, claimId }} />
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <span className="brand-mark" aria-hidden>▣</span>
          Claims pipeline
        </NavLink>
        <nav className="nav">
          <NavLink to="/" end>Claims</NavLink>
          <NavLink to="/claims/new">Submit</NavLink>
          <NavLink to="/benchmarks">Benchmarks</NavLink>
          <NavLink to="/policies">Policies</NavLink>
        </nav>
        <div className="topbar-status" title={health ? JSON.stringify(health.components, null, 2) : 'API unreachable'}>
          <span className={`dot ${health ? 'ok' : 'bad'}`} />
          {health ? `backend: ${health.backend}` : 'API offline'}
        </div>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<ClaimsList />} />
          <Route path="/claims/new" element={<SubmitClaim health={health} />} />
          <Route path="/claims/:id" element={<StoreClaim />} />
          <Route path="/benchmarks" element={<BenchmarkPage />} />
          <Route path="/benchmarks/:run" element={<BenchmarkPage />} />
          <Route path="/benchmarks/:run/claims/:claimId" element={<BenchmarkClaim />} />
          <Route path="/policies" element={<PolicyPage />} />
          <Route path="/policies/:name" element={<PolicyPage />} />
        </Routes>
      </main>
    </div>
  )
}
