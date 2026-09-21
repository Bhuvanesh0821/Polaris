import { useMemo, useState } from 'react'
import { Activity, BrainCircuit, TrendingUp } from 'lucide-react'
import { usePolaris, useEndpoint } from '../hooks/usePolaris'
import api from '../services/api'
import {
  Card, DataTag, Empty, ErrorState, Loading, Note, Stat, Tag,
} from '../components/Primitives'
import { ForecastBandChart, Legend2 } from '../charts/Charts'
import { num, utc } from '../utils/format'

const HORIZONS = [
  { h: 6, label: '6 h' },
  { h: 24, label: '24 h' },
  { h: 48, label: '48 h' },
  { h: 168, label: '7 d' },
]

export default function LoadForecast() {
  const { tick } = usePolaris()
  const [hours, setHours] = useState(48)
  const { data, loading, error, reload } = useEndpoint(
    () => api.loadForecast(hours), [hours, tick],
  )

  const chart = useMemo(() => (data || []).map((r) => ({
    time: r.target_time,
    value: r.predicted_load_kw,
    p10: r.load_kw_p10,
    p90: r.load_kw_p90,
    critical: r.critical_load_kw,
    deferrable: r.deferrable_load_kw,
  })), [data])

  const stats = useMemo(() => {
    if (!data?.length) return null
    const v = data.map((r) => r.predicted_load_kw)
    const peak = data[v.indexOf(Math.max(...v))]
    const trough = data[v.indexOf(Math.min(...v))]
    return {
      mean: v.reduce((a, b) => a + b, 0) / v.length,
      peak, trough,
      energy: v.reduce((a, b) => a + b, 0),
      criticalMean: data.reduce((a, r) => a + r.critical_load_kw, 0) / data.length,
    }
  }, [data])

  const model = data?.[0]

  return (
    <div className="page">
      <div className="page-intro">
        <h2>AI load forecasting</h2>
        <p>
          A gradient-boosted regressor predicts station demand from the real weather forecast.
          The shaded band is the 80% prediction interval derived from training residuals.
        </p>
      </div>

      <div className="mb-4">
        <Note kind="info" icon={<BrainCircuit size={14} />}>
          <strong>How to read this.</strong> Inputs are <em>real</em> forecast weather. The
          target the model learned is <em>modelled</em> station load from the POLARIS energy
          model — no public station telemetry exists to train on. The output is therefore an
          AI forecast of a modelled quantity, and is labelled as such throughout.
        </Note>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={reload}
          hint="Forecasts appear after the first successful refresh." />
      ) : loading ? (
        <Card><Loading rows={5} /></Card>
      ) : !data?.length ? (
        <Card><Empty message="No forecast generated yet."
          hint="Press Refresh in the header." /></Card>
      ) : (
        <>
          <div className="grid grid-5 mb-4">
            <Card bodyClass="stat-card">
              <Stat label="Mean predicted load" value={num(stats.mean, 1)} unit="kW" size="sm"
                sub={`over ${hours} h`} />
            </Card>
            <Card bodyClass="stat-card">
              <Stat label="Peak demand" value={num(stats.peak.predicted_load_kw, 1)} unit="kW" size="sm"
                sub={utc(stats.peak.target_time)} tone="warn" />
            </Card>
            <Card bodyClass="stat-card">
              <Stat label="Minimum demand" value={num(stats.trough.predicted_load_kw, 1)} unit="kW" size="sm"
                sub={utc(stats.trough.target_time)} />
            </Card>
            <Card bodyClass="stat-card">
              <Stat label="Forecast energy" value={num(stats.energy, 0)} unit="kWh" size="sm"
                sub={`${hours} h horizon`} />
            </Card>
            <Card bodyClass="stat-card">
              <Stat label="Mean critical load" value={num(stats.criticalMean, 1)} unit="kW" size="sm"
                sub="must be served at all times" tone="crit" />
            </Card>
          </div>

          <Card
            title="Predicted station demand"
            icon={<TrendingUp size={14} />}
            tag={<DataTag kind="AI_FORECAST" />}
            actions={
              <div className="seg-control">
                {HORIZONS.map((o) => (
                  <button key={o.h} className={hours === o.h ? 'active' : ''}
                    onClick={() => setHours(o.h)}>{o.label}</button>
                ))}
              </div>
            }
            footer={model && (
              <div className="row-between wrap gap-3">
                <span>Model: <span className="mono">{model.model_version}</span></span>
                <span>Weather input: {model.weather_provenance}</span>
              </div>
            )}
          >
            <ForecastBandChart
              data={chart} height={320} color="#0b4f3f" name="Predicted load"
              extraLines={[
                { key: 'critical', name: 'Critical load', color: '#b3261e', dash: '4 3' },
                { key: 'deferrable', name: 'Deferrable', color: '#7fd4e2' },
              ]}
            />
            <Legend2 items={[
              { label: 'Predicted load', color: '#0b4f3f' },
              { label: '80% interval', color: 'rgba(11,79,63,0.2)' },
              { label: 'Critical load', color: '#b3261e' },
              { label: 'Deferrable', color: '#7fd4e2' },
            ]} />
          </Card>

          <div className="mt-4">
            <Card title="Hourly forecast" icon={<Activity size={14} />} bodyClass="flush">
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Target (UTC)</th>
                      <th className="num">+h</th>
                      <th className="num">Predicted</th>
                      <th className="num">P10</th>
                      <th className="num">P90</th>
                      <th className="num">Critical</th>
                      <th className="num">Deferrable</th>
                      <th className="num">Temp in</th>
                      <th className="num">Wind in</th>
                      <th>Weather source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.map((r, i) => (
                      <tr key={i}>
                        <td className="mono tiny">{utc(r.target_time)}</td>
                        <td className="num tiny">{r.horizon_h}</td>
                        <td className="num strong">{num(r.predicted_load_kw, 1)}</td>
                        <td className="num faint">{num(r.load_kw_p10, 1)}</td>
                        <td className="num faint">{num(r.load_kw_p90, 1)}</td>
                        <td className="num">{num(r.critical_load_kw, 1)}</td>
                        <td className="num">{num(r.deferrable_load_kw, 1)}</td>
                        <td className="num tiny">{num(r.input_temperature_c, 1)}</td>
                        <td className="num tiny">{num(r.input_wind_speed_ms, 1)}</td>
                        <td>
                          <Tag kind={r.weather_provenance === 'REAL_FORECAST' ? 'ai' : 'real'}>
                            {r.weather_provenance === 'REAL_FORECAST' ? 'NWP FORECAST' : 'OBSERVED'}
                          </Tag>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
