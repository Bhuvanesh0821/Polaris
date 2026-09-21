import { Clock, RefreshCw, Server, Settings as Cog, Wifi } from 'lucide-react'
import { usePolaris, useEndpoint } from '../hooks/usePolaris'
import api from '../services/api'
import {
  Card, KV, Loading, LivePill, Note, Stat, Tag, ErrorState,
} from '../components/Primitives'
import { ago, int, num, utc } from '../utils/format'

const POLL_OPTIONS = [
  { label: '30 s', ms: 30000 },
  { label: '1 min', ms: 60000 },
  { label: '5 min', ms: 300000 },
  { label: 'Off', ms: 0 },
]

export default function Settings() {
  const {
    autoPoll, setAutoPoll, pollMs, setPollMs, refreshNow, refreshing,
    liveStatus, ageSeconds, scheduler, tick, refreshResult,
  } = usePolaris()

  const health = useEndpoint(() => api.health(), [tick])
  const sources = useEndpoint(() => api.sources(), [tick])

  function setPoll(ms) {
    if (ms === 0) { setAutoPoll(false) } else { setAutoPoll(true); setPollMs(ms) }
  }

  const h = health.data

  return (
    <div className="page">
      <div className="page-intro">
        <h2>Settings &amp; system status</h2>
        <p>
          Refresh behaviour, backend health and live-source configuration.
        </p>
      </div>

      <div className="grid grid-3 mb-4">
        <Card title="Browser refresh" icon={<RefreshCw size={14} />}>
          <div className="field mb-3">
            <label>Dashboard polling interval</label>
            <div className="seg-control">
              {POLL_OPTIONS.map((o) => (
                <button key={o.ms}
                  className={(o.ms === 0 ? !autoPoll : autoPoll && pollMs === o.ms) ? 'active' : ''}
                  onClick={() => setPoll(o.ms)}>{o.label}</button>
              ))}
            </div>
            <span className="tiny muted">
              How often this browser re-reads the API. This does not change how often the
              backend pulls from live weather providers.
            </span>
          </div>

          <button className="btn primary" onClick={refreshNow} disabled={refreshing}
            style={{ width: '100%' }}>
            <RefreshCw size={14} className={refreshing ? 'spin' : ''} />
            {refreshing ? 'Refreshing…' : 'Force live refresh now'}
          </button>
          <div className="tiny muted mt-2">
            Pulls every provider immediately and reruns the full AI pipeline.
          </div>

          {refreshResult && (
            <div className="mt-3">
              <Note kind={refreshResult.ok ? 'info' : 'crit'}>
                <strong>{refreshResult.ok ? 'Refresh succeeded.' : 'Refresh failed.'}</strong>{' '}
                {refreshResult.message}
                {refreshResult.ingestion && (
                  <div className="tiny mt-2">
                    Active source: {refreshResult.ingestion.active_provider || 'none'} ·{' '}
                    {refreshResult.ingestion.written} observations written ·{' '}
                    {num(refreshResult.duration_ms, 0)} ms
                  </div>
                )}
              </Note>
            </div>
          )}
        </Card>

        <Card title="Backend refresh scheduler" icon={<Clock size={14} />}>
          <KV k="Running" v={scheduler?.running ? 'Yes' : 'No'} />
          <KV k="Interval" v={`${Math.round((scheduler?.interval_seconds || 0) / 60)} min`} />
          <KV k="Last run" v={scheduler?.last_run_at ? utc(scheduler.last_run_at) : '—'} />
          <KV k="Last success" v={scheduler?.last_success_at ? utc(scheduler.last_success_at) : '—'} />
          <KV k="Next run" v={
            scheduler?.seconds_until_next != null
              ? `in ${Math.round(scheduler.seconds_until_next / 60)} min`
              : '—'
          } />
          <KV k="Runs / failures" v={`${int(scheduler?.run_count)} / ${int(scheduler?.failure_count)}`} />
          {scheduler?.consecutive_failures > 0 && (
            <div className="mt-2">
              <Tag kind="warn">{scheduler.consecutive_failures} consecutive failures</Tag>
              <div className="tiny muted mt-2">
                The scheduler backs off automatically after repeated failures so a dead
                provider is not hammered.
              </div>
            </div>
          )}
          {scheduler?.last_error && (
            <div className="tiny mt-2" style={{ color: 'var(--pol-crit)' }}>
              {scheduler.last_error.slice(0, 200)}
            </div>
          )}
        </Card>

        <Card title="System health" icon={<Server size={14} />}
          actions={<LivePill status={liveStatus} ageSeconds={ageSeconds} compact />}>
          {health.loading ? <Loading rows={3} />
            : health.error ? <ErrorState error={health.error} onRetry={health.reload} />
              : h ? (
                <>
                  <div className="row-between mb-3">
                    <Stat label="API status" value={h.status.toUpperCase()} size="sm"
                      tone={h.status === 'ok' ? 'ok' : h.status === 'degraded' ? 'warn' : 'crit'} />
                    <Stat label="Uptime" value={ago(h.uptime_seconds).replace(' ago', '')} size="sm" />
                  </div>
                  <KV k="Version" v={h.version} />
                  <KV k="Database" v={h.database_connected ? 'Connected' : 'Disconnected'} />
                  <KV k="PostgreSQL" v={h.database_version || '—'} />
                  <KV k="DSN" v={h.database_dsn || '—'} />
                  <KV k="Models trained" v={h.models_trained ? 'Yes' : 'No'} />
                  <KV k="Live weather" v={h.live_weather_status || '—'} />
                  <KV k="Last observation" v={h.last_weather_update ? utc(h.last_weather_update) : '—'} />
                </>
              ) : null}
        </Card>
      </div>

      <Card title="Live data sources" icon={<Wifi size={14} />} bodyClass="flush" className="mb-4">
        <table className="table">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Priority</th>
              <th>Health</th>
              <th className="num">Success</th>
              <th className="num">Latency</th>
              <th className="num">Observations</th>
              <th>Last success</th>
              <th>Solar radiation</th>
            </tr>
          </thead>
          <tbody>
            {(sources.data || []).map((s) => (
              <tr key={s.provider_key}>
                <td>
                  <div className="strong small">{s.provider_label}</div>
                  <div className="tiny muted mono">{s.provider_key}</div>
                </td>
                <td className="num">{s.priority}</td>
                <td>
                  <span className="row gap-2">
                    <span className={`health-dot health-${s.health}`} />
                    <span className="tiny">{s.health}</span>
                    {s.is_active_source && <Tag kind="ok">ACTIVE</Tag>}
                  </span>
                </td>
                <td className="num tiny">{s.total_successes}/{s.total_attempts}</td>
                <td className="num tiny">{s.last_latency_ms ? `${Math.round(s.last_latency_ms)} ms` : '—'}</td>
                <td className="num">{int(s.total_observations)}</td>
                <td className="tiny mono">{s.last_success_at ? utc(s.last_success_at) : '—'}</td>
                <td>
                  {s.supports_solar_radiation
                    ? <Tag kind="ok">YES</Tag>
                    : <Tag kind="neutral">NOT REPORTED</Tag>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Note kind="info" icon={<Cog size={14} />}>
        <strong>Thresholds and plant parameters</strong> are defined server-side in{' '}
        <span className="mono">backend/app/core/station.py</span> so that the model and the
        dashboard can never disagree. Change them there and restart the backend; the Data &amp;
        Model page always shows the values actually in force.
      </Note>
    </div>
  )
}
