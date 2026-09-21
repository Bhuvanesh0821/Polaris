import {
  AlertTriangle, CheckCircle2, HelpCircle, Info, Loader2, RadioTower, Inbox,
  ShieldAlert,
} from 'lucide-react'
import { ago, num, utc } from '../utils/format'

/* ------------------------------------------------------------------ card --- */

export function Card({ title, icon, actions, children, className = '', bodyClass = '', footer, tag }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {icon && <span className="icon">{icon}</span>}
          {title && <h3>{title}</h3>}
          {tag}
          <span className="spacer" />
          {actions}
        </header>
      )}
      <div className={`card-body ${bodyClass}`}>{children}</div>
      {footer && <footer className="card-foot">{footer}</footer>}
    </section>
  )
}

/* ------------------------------------------------------------------ tags --- */

export function Tag({ kind = 'neutral', children, title }) {
  return <span className={`tag ${kind}`} title={title}>{children}</span>
}

/**
 * The four data classes, rendered identically everywhere so the distinction
 * is impossible to miss.
 */
export function DataTag({ kind }) {
  const map = {
    REAL_LIVE_WEATHER: ['real', 'REAL LIVE DATA', 'Measured observation from an external provider'],
    MODELLED_ENERGY: ['modelled', 'MODELLED', 'Research-based energy model estimate — not telemetry'],
    AI_FORECAST: ['ai', 'AI FORECAST', 'Predicted by scikit-learn from real weather inputs'],
    SIMULATED_SCENARIO: ['sim', 'SIMULATED', 'What-if scenario — not live station telemetry'],
  }
  const [cls, label, title] = map[kind] || ['neutral', kind, '']
  return <span className={`tag ${cls}`} title={title}>{label}</span>
}

/* ------------------------------------------------------------ live pill --- */

export function LivePill({ status, ageSeconds, compact = false }) {
  const map = {
    LIVE: ['live', 'LIVE'],
    STALE: ['stale', 'STALE'],
    FAILED: ['failed', 'SOURCE DOWN'],
    LOADING: ['loading', 'CONNECTING'],
  }
  const [cls, label] = map[status] || map.LOADING
  return (
    <span className={`live-pill ${cls}`} title={
      status === 'LIVE' ? 'Receiving fresh observations'
        : status === 'STALE' ? 'Live source unavailable — showing last successful observation'
          : status === 'FAILED' ? 'No live source reachable'
            : 'Contacting the backend'
    }>
      <span className="live-dot" />
      {label}
      {!compact && ageSeconds != null && status !== 'LOADING' && (
        <span className="pill-age" style={{ fontWeight: 500, opacity: 0.8 }}>
          · {ago(ageSeconds)}
        </span>
      )}
    </span>
  )
}

/* ------------------------------------------------------------------ stat --- */

export function Stat({ label, value, unit, sub, size = '', icon, tone, delta }) {
  const toneColor = {
    ok: 'var(--pol-ok)', warn: 'var(--pol-warn)',
    crit: 'var(--pol-crit)', emerg: 'var(--pol-emerg)',
  }[tone]
  return (
    <div className="stat">
      <span className="stat-label">{icon}{label}</span>
      <span className={`stat-value ${size}`} style={toneColor ? { color: toneColor } : undefined}>
        {value}
        {unit && <span className="stat-unit">{unit}</span>}
      </span>
      {delta && <span className={`stat-delta ${delta.dir}`}>{delta.text}</span>}
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  )
}

/**
 * A measured value with its source attribution underneath. This is the
 * component that makes "where did this number come from" answerable at a
 * glance for every live reading.
 */
export function MeasuredStat({ label, field, unit, digits = 1, icon, transform }) {
  const value = field?.value
  const shown = transform
    ? transform(value)
    : (value === null || value === undefined ? '—' : num(value, digits))
  return (
    <div className="stat">
      <span className="stat-label">{icon}{label}</span>
      <span className="stat-value">
        {shown}
        {unit && value !== null && value !== undefined && <span className="stat-unit">{unit}</span>}
      </span>
      {field?.source ? (
        <span className="attr" title={`Observed ${utc(field.observed_at)} · ${field.source}`}>
          <RadioTower size={10} />
          <span className="truncate">{field.source}</span>
        </span>
      ) : (
        <span className="attr" title="No source reports this variable right now">
          <Info size={10} /> not reported by any active source
        </span>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ bars --- */

export function Bar({ value, max = 100, color = 'var(--pol-green-600)', markers = [] }) {
  const pctv = Math.max(0, Math.min(100, (value / max) * 100))
  return (
    <div className="bar-track">
      <div className="bar-fill" style={{ width: `${pctv}%`, background: color }} />
      {markers.map((m, i) => (
        <div key={i} className="bar-marker" style={{ left: `${(m / max) * 100}%` }} title={`Threshold ${m}`} />
      ))}
    </div>
  )
}

export function StackBar({ segments }) {
  const total = segments.reduce((s, x) => s + Math.max(0, x.value), 0) || 1
  return (
    <div className="stack-bar">
      {segments.map((s, i) => (
        <div
          key={i}
          className="stack-seg"
          style={{ width: `${(Math.max(0, s.value) / total) * 100}%`, background: s.color }}
          title={`${s.label}: ${num(s.value, 1)}`}
        />
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------- kv --- */

export function KV({ k, v, title }) {
  return (
    <div className="kv" title={title}>
      <span className="kv-key">{k}</span>
      <span className="kv-val">{v}</span>
    </div>
  )
}

/* --------------------------------------------------------------- notices --- */

export function Note({ kind = 'info', children, icon }) {
  const defaultIcon = {
    info: <Info size={14} />,
    warn: <AlertTriangle size={14} />,
    crit: <ShieldAlert size={14} />,
    model: <Info size={14} />,
  }[kind]
  return (
    <div className={`banner-note ${kind}`}>
      {icon || defaultIcon}
      <div>{children}</div>
    </div>
  )
}

export function SimBanner({ children }) {
  return (
    <div className="sim-banner">
      <AlertTriangle size={14} />
      {children || 'SIMULATED SCENARIO — NOT LIVE STATION TELEMETRY'}
    </div>
  )
}

/* ------------------------------------------------------------ load states --- */

export function Loading({ label = 'Loading…', rows = 3 }) {
  return (
    <div>
      <div className="row gap-2 muted small mb-3">
        <Loader2 size={13} className="spin" /> {label}
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 14, marginBottom: 8, width: `${92 - i * 11}%` }} />
      ))}
    </div>
  )
}

export function ErrorState({ error, onRetry, hint }) {
  return (
    <div className="banner-note crit">
      <AlertTriangle size={15} />
      <div style={{ flex: 1 }}>
        <div className="strong">{error}</div>
        {hint && <div className="small mt-2" style={{ opacity: 0.9 }}>{hint}</div>}
        {onRetry && (
          <button className="btn sm mt-2" onClick={onRetry}>Retry</button>
        )}
      </div>
    </div>
  )
}

export function Empty({ message, hint, icon }) {
  return (
    <div className="empty">
      {icon || <Inbox size={26} />}
      <div>{message}</div>
      {hint && <div className="tiny mt-2">{hint}</div>}
    </div>
  )
}

/**
 * "WHY THIS ACTION?" — the explanation block attached to any recommendation.
 * Every line is generated server-side from live model values.
 */
export function WhyThisAction({ reasons, compact = false }) {
  if (!reasons?.length) return null
  return (
    <div className={`why-block ${compact ? 'compact' : ''}`}>
      <div className="why-head">
        <HelpCircle size={12} /> WHY THIS ACTION?
      </div>
      <ul className="why-list">
        {reasons.map((r, i) => <li key={i}>{r}</li>)}
      </ul>
    </div>
  )
}

/** Risk level badge with its numeric score. */
export function RiskBadge({ level, scorePct, color }) {
  if (!level) return null
  return (
    <span className="risk-badge" style={{ borderColor: color, color }}>
      <span className="risk-dot" style={{ background: color }} />
      {level}
      {scorePct != null && <span className="risk-score">{Math.round(scorePct)}</span>}
    </span>
  )
}

export function Ok({ children }) {
  return (
    <div className="row gap-2 small" style={{ color: 'var(--pol-ok)' }}>
      <CheckCircle2 size={14} /> {children}
    </div>
  )
}
