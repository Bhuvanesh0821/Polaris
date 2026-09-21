import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Activity, AlertTriangle, BrainCircuit, Cloud, Compass,
  Database, Gauge, LayoutDashboard, Menu, RefreshCw, Settings, ShieldAlert,
  Sliders, Sun, Timer,
} from 'lucide-react'
import { usePolaris } from '../hooks/usePolaris'
import { LivePill } from '../components/Primitives'
import { utc } from '../utils/format'

const NAV = [
  {
    group: 'Operations',
    items: [
      { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
      { to: '/energy', label: 'Energy Dashboard', icon: Gauge },
      { to: '/weather', label: 'Live Weather', icon: Cloud },
      { to: '/alerts', label: 'Alerts', icon: AlertTriangle, badge: 'alerts' },
    ],
  },
  {
    group: 'Forecasting',
    items: [
      { to: '/load-forecast', label: 'Load Forecast', icon: Activity },
      { to: '/renewable-forecast', label: 'Renewable Forecast', icon: Sun },
    ],
  },
  {
    group: 'Decision Support',
    items: [
      { to: '/optimization', label: 'Energy Optimization', icon: Sliders },
      { to: '/survival', label: 'Survival Analysis', icon: Timer },
      { to: '/crisis', label: 'Crisis Simulator', icon: ShieldAlert },
      { to: '/explainability', label: 'Explainable AI', icon: BrainCircuit },
    ],
  },
  {
    group: 'System',
    items: [
      { to: '/data-model', label: 'Data & Model', icon: Database },
      { to: '/settings', label: 'Settings', icon: Settings },
    ],
  },
]

const TITLES = {
  '/': ['Operational Overview', 'Station status at a glance'],
  '/energy': ['Energy Dashboard', 'Modelled generation, storage and demand'],
  '/weather': ['Live Weather', 'Real observations from Antarctic sources'],
  '/alerts': ['Alerts', 'Threshold and anomaly conditions'],
  '/load-forecast': ['Load Forecast', 'AI prediction of station demand'],
  '/renewable-forecast': ['Renewable Forecast', 'Hybrid physics + ML generation prediction'],
  '/optimization': ['Energy Optimization', 'Dispatch planning and fuel minimisation'],
  '/survival': ['Survival Analysis', 'Estimated energy autonomy'],
  '/crisis': ['Polar Crisis Simulator', 'What-if resilience scenarios'],
  '/explainability': ['Explainable AI', 'Why the models decided what they decided'],
  '/data-model': ['Data & Model', 'Provenance, coverage and model cards'],
  '/settings': ['Settings', 'Refresh behaviour and thresholds'],
}

export default function AppLayout() {
  const { liveStatus, ageSeconds, observedAt, primarySource, refreshNow, refreshing, alerts, station, banners } =
    usePolaris()
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  const [title, subtitle] = TITLES[loc.pathname] || ['POLARIS', '']

  const counts = { alerts: alerts?.length || 0 }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="sidebar-brand">
          <div className="brand-row">
            <span className="brand-mark"><Compass size={17} /></span>
            <div>
              <div className="brand-name">POLARIS</div>
              <div className="brand-sub">Polar Intelligent Energy Management<br />&amp; Resilience Intelligence System</div>
            </div>
          </div>
        </div>

        <nav className="sidebar-scroll">
          {NAV.map((g) => (
            <div key={g.group}>
              <div className="nav-group-label">{g.group}</div>
              {g.items.map((it) => {
                const Icon = it.icon
                const badge = it.badge ? counts[it.badge] : 0
                return (
                  <NavLink
                    key={it.to}
                    to={it.to}
                    end={it.end}
                    className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                    onClick={() => setOpen(false)}
                  >
                    <Icon size={15} />
                    <span>{it.label}</span>
                    {badge > 0 && <span className="nav-badge">{badge}</span>}
                  </NavLink>
                )
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div style={{ color: '#9ecabb', fontWeight: 600, letterSpacing: '0.04em' }}>
            {station?.station_name || 'Maitri Research Station'}
          </div>
          <div style={{ marginTop: 2 }}>
            WMO {station?.wmo_index || '89514'} · {station?.latitude?.toFixed(2) ?? '−70.77'}°,{' '}
            {station?.longitude?.toFixed(2) ?? '11.75'}°
          </div>
          <div style={{ marginTop: 5, opacity: 0.75 }}>
            Python · FastAPI · React · PostgreSQL
          </div>
        </div>
      </aside>

      <div className="main-area">
        <div className="data-banner">
          <strong>{banners?.data_banner || 'REAL WEATHER DATA + RESEARCH-BASED ENERGY MODEL'}</strong>
          <span className="sep">|</span>
          <span className="note">
            {banners?.model_disclaimer
              || 'Station energy parameters are modelled/estimated for research and demonstration purposes.'}
          </span>
        </div>

        <header className="topbar">
          <button className="btn icon-only nav-toggle" onClick={() => setOpen((v) => !v)}
            aria-label="Toggle navigation" aria-expanded={open}>
            <Menu size={16} />
          </button>

          <div className="topbar-title">
            <h1>{title}</h1>
            <div className="crumb">{subtitle}</div>
          </div>

          <div className="topbar-meta">
            <LivePill status={liveStatus} ageSeconds={ageSeconds} />

            <div className="meta-block meta-updated">
              <span className="meta-label">Last updated</span>
              <span className="meta-value mono" title={utc(observedAt, true)}>
                {observedAt ? utc(observedAt) : '—'}
              </span>
            </div>

            <div className="meta-block meta-source">
              <span className="meta-label">Data source</span>
              <span className="meta-value truncate" title={primarySource || 'No source'}>
                {primarySource || '—'}
              </span>
            </div>

            <button className="btn primary" onClick={refreshNow} disabled={refreshing}>
              <RefreshCw size={14} className={refreshing ? 'spin' : ''} />
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        </header>

        <main>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
