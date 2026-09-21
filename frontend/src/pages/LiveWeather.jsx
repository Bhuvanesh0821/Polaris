import { useMemo, useState } from 'react'
import {
  CloudOff, Compass, Database, Droplets, Gauge, Radio, RefreshCw, Satellite,
  Sun, Thermometer, Wind,
} from 'lucide-react'
import { usePolaris, useEndpoint } from '../hooks/usePolaris'
import api from '../services/api'
import {
  Card, DataTag, Empty, ErrorState, KV, LivePill, Loading, MeasuredStat, Note, Tag,
} from '../components/Primitives'
import { WeatherChart } from '../charts/Charts'
import { ago, compassPoint, int, num, utc } from '../utils/format'

const RANGES = [
  { label: '24 h', hours: 24 },
  { label: '48 h', hours: 48 },
  { label: '7 d', hours: 168 },
  { label: '30 d', hours: 720 },
]

export default function LiveWeather() {
  const {
    tick, liveStatus, ageSeconds, observedAt, weatherFields: W, summary,
    refreshNow, refreshing,
  } = usePolaris()
  const [hours, setHours] = useState(48)

  const history = useEndpoint(() => api.weatherHistory(hours, false), [hours, tick])
  const sources = useEndpoint(() => api.sources(), [tick])
  const providers = useEndpoint(() => api.providers(), [])

  const chartData = useMemo(() => (history.data || []).map((r) => ({
    time: r.observed_at,
    temperature: r.temperature_c,
    windChill: r.wind_chill_c,
    wind: r.wind_speed_ms,
    solar: r.solar_radiation_wm2,
  })), [history.data])

  const live = summary?.live_weather
  const stale = liveStatus !== 'LIVE'

  return (
    <div className="page">
      <div className="page-intro">
        <h2>Real Antarctic observations</h2>
        <p>
          Every value on this page is a measurement retrieved from an external meteorological
          provider. POLARIS never generates a weather value — if all providers fail, it reports
          the failure and re-serves the last genuine observation.
        </p>
      </div>

      {stale && (
        <div className="mb-4">
          <Note kind="warn" icon={<CloudOff size={14} />}>
            <strong>Live source unavailable — displaying last successful observation.</strong>{' '}
            Observed {utc(observedAt)} ({ago(ageSeconds)}).
          </Note>
        </div>
      )}

      <Card
        className="accent-green mb-4"
        title="Current conditions"
        icon={<Radio size={14} />}
        tag={<DataTag kind="REAL_LIVE_WEATHER" />}
        actions={
          <div className="row gap-3">
            <LivePill status={liveStatus} ageSeconds={ageSeconds} />
            <button className="btn primary sm" onClick={refreshNow} disabled={refreshing}>
              <RefreshCw size={12} className={refreshing ? 'spin' : ''} /> Refresh
            </button>
          </div>
        }
        footer={
          live?.contributing_sources?.length ? (
            <div>
              <span className="strong">Contributing sources: </span>
              {live.contributing_sources.join(' · ')}
            </div>
          ) : null
        }
      >
        <div className="grid grid-6 mb-4">
          <MeasuredStat label="Temperature" field={W.temperature_c} unit="°C" icon={<Thermometer size={11} />} />
          <MeasuredStat label="Wind speed" field={W.wind_speed_ms} unit="m/s" icon={<Wind size={11} />} />
          <MeasuredStat label="Wind direction" field={W.wind_direction_deg} icon={<Compass size={11} />}
            transform={(v) => (v == null ? '—' : `${compassPoint(v)} ${Math.round(v)}°`)} />
          <MeasuredStat label="Solar radiation" field={W.solar_radiation_wm2} unit="W/m²" digits={0} icon={<Sun size={11} />} />
          <MeasuredStat label="Humidity" field={W.humidity_pct} unit="%" digits={0} icon={<Droplets size={11} />} />
          <MeasuredStat label="Pressure" field={W.pressure_hpa} unit="hPa" digits={1} icon={<Gauge size={11} />} />
        </div>
        <div className="grid grid-6">
          <MeasuredStat label="Wind gust" field={W.wind_gust_ms} unit="m/s" icon={<Wind size={11} />} />
          <MeasuredStat label="Wind chill" field={W.wind_chill_c} unit="°C" icon={<Thermometer size={11} />} />
          <MeasuredStat label="Apparent temp" field={W.apparent_temperature_c} unit="°C" icon={<Thermometer size={11} />} />
          <MeasuredStat label="Cloud cover" field={W.cloud_cover_pct} unit="%" digits={0} />
          <MeasuredStat label="Snowfall" field={W.snowfall_mm} unit="mm" digits={1} />
          <MeasuredStat label="Air density" field={W.air_density_kg_m3} unit="kg/m³" digits={3} />
        </div>
      </Card>

      <div className="grid grid-3-2 mb-4">
        <Card
          title={`Observed conditions · last ${hours < 48 ? `${hours} h` : `${Math.round(hours / 24)} d`}`}
          icon={<Thermometer size={14} />}
          tag={<DataTag kind="REAL_LIVE_WEATHER" />}
          actions={
            <div className="seg-control">
              {RANGES.map((r) => (
                <button key={r.hours} className={hours === r.hours ? 'active' : ''}
                  onClick={() => setHours(r.hours)}>{r.label}</button>
              ))}
            </div>
          }
        >
          {history.loading ? <Loading rows={4} />
            : history.error ? <ErrorState error={history.error} onRetry={history.reload} />
              : chartData.length ? <WeatherChart data={chartData} height={290} />
                : <Empty message="No observations stored yet." hint="Press Refresh to ingest live data." />}
        </Card>

        <Card title="Data source health" icon={<Satellite size={14} />} bodyClass="tight">
          {sources.loading ? <Loading rows={3} />
            : (sources.data || []).length === 0 ? (
              <Empty message="No provider has been contacted yet." />
            ) : (
              <div>
                {(sources.data || []).map((s) => (
                  <div className="source-row" key={s.provider_key}>
                    <span className={`health-dot health-${s.health}`} />
                    <div className="grow">
                      <div className="row-between gap-2">
                        <span className="strong small truncate" title={s.provider_label}>
                          {s.provider_label}
                        </span>
                        {s.is_active_source && <Tag kind="ok">ACTIVE</Tag>}
                      </div>
                      <div className="tiny muted">
                        {s.health} · {s.total_successes}/{s.total_attempts} ok ·{' '}
                        {s.last_latency_ms ? `${Math.round(s.last_latency_ms)} ms` : '—'} ·{' '}
                        {s.total_observations} obs
                      </div>
                      {s.last_error && (
                        <div className="tiny" style={{ color: 'var(--pol-crit)' }}>
                          {s.last_error.slice(0, 110)}
                        </div>
                      )}
                      {!s.supports_solar_radiation && (
                        <div className="tiny faint">does not report solar radiation</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Provider catalogue" icon={<Database size={14} />} bodyClass="tight">
          {providers.loading ? <Loading rows={3} /> : (
            <div>
              {Object.entries(providers.data?.providers || {}).map(([key, p]) => (
                <div key={key} style={{ padding: '10px 0', borderBottom: '1px solid var(--pol-line-soft)' }}>
                  <div className="row-between gap-2 mb-2">
                    <span className="strong small">{p.label}</span>
                    {p.authoritative && <Tag kind="ok">AUTHORITATIVE</Tag>}
                  </div>
                  <div className="tiny muted">{p.kind}</div>
                  <KV k="Station" v={p.station} />
                  <KV k="Cadence" v={p.cadence} />
                  <div className="row gap-2 wrap mt-2">
                    {(p.variables || []).map((v) => (
                      <span className="driver-chip" key={v}>{v}</span>
                    ))}
                  </div>
                  {(p.missing || []).length > 0 && (
                    <div className="tiny mt-2" style={{ color: 'var(--pol-warn)' }}>
                      Not reported: {p.missing.join(', ')}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Observation log" icon={<Radio size={14} />} bodyClass="flush">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Observed (UTC)</th>
                  <th className="num">Temp</th>
                  <th className="num">Wind</th>
                  <th className="num">Dir</th>
                  <th className="num">Solar</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {[...(history.data || [])].reverse().slice(0, 120).map((r) => (
                  <tr key={r.id}>
                    <td className="mono tiny">{utc(r.observed_at)}</td>
                    <td className="num">{num(r.temperature_c, 1)}</td>
                    <td className="num">{num(r.wind_speed_ms, 1)}</td>
                    <td className="num tiny">{r.wind_direction_deg != null ? compassPoint(r.wind_direction_deg) : '—'}</td>
                    <td className="num">{r.solar_radiation_wm2 != null ? int(r.solar_radiation_wm2) : '—'}</td>
                    <td className="tiny truncate" style={{ maxWidth: 180 }} title={r.source}>
                      {r.source_provider}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {providers.data?.note && (
        <div className="mt-4">
          <Note kind="info">{providers.data.note}</Note>
        </div>
      )}
    </div>
  )
}
