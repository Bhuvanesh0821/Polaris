import { useMemo, useState } from 'react'
import { Snowflake, Sun, Wind, Zap } from 'lucide-react'
import { usePolaris, useEndpoint } from '../hooks/usePolaris'
import api from '../services/api'
import {
  Card, DataTag, Empty, ErrorState, Loading, Note, Stat, Tag,
} from '../components/Primitives'
import { ForecastBandChart, GenerationMixChart, Legend2 } from '../charts/Charts'
import { num, utc } from '../utils/format'

const HORIZONS = [
  { h: 6, label: '6 h' },
  { h: 24, label: '24 h' },
  { h: 48, label: '48 h' },
  { h: 168, label: '7 d' },
]

export default function RenewableForecast() {
  const { tick } = usePolaris()
  const [hours, setHours] = useState(48)
  const { data, loading, error, reload } = useEndpoint(
    () => api.renewableForecast(hours), [hours, tick],
  )

  const chart = useMemo(() => (data || []).map((r) => ({
    time: r.target_time,
    wind: r.wind_kw,
    solar: r.solar_kw,
    generator: 0,
    total: r.total_kw,
    value: r.total_kw,
    p10: (r.wind_kw_p10 ?? 0) + (r.solar_kw_p10 ?? 0),
    p90: (r.wind_kw_p90 ?? 0) + (r.solar_kw_p90 ?? 0),
    physical: (r.wind_physical_kw ?? 0) + (r.solar_physical_kw ?? 0),
  })), [data])

  const stats = useMemo(() => {
    if (!data?.length) return null
    const tot = data.map((r) => r.total_kw)
    return {
      windEnergy: data.reduce((a, r) => a + r.wind_kw, 0),
      solarEnergy: data.reduce((a, r) => a + r.solar_kw, 0),
      peak: Math.max(...tot),
      mean: tot.reduce((a, b) => a + b, 0) / tot.length,
      icingHours: data.filter((r) => r.icing_risk).length,
      curtailHours: data.filter((r) => r.turbine_curtailed).length,
      mlShift: data.reduce((a, r) => a + Math.abs(r.ml_correction_kw ?? 0), 0) / data.length,
    }
  }, [data])

  return (
    <div className="page">
      <div className="page-intro">
        <h2>Renewable generation prediction</h2>
        <p>
          A hybrid model: a deterministic physical baseline (turbine power curve with
          air-density correction, PV with plane-of-array transposition and cell-temperature
          derating) plus a learned residual correction from a random forest.
        </p>
      </div>

      <div className="mb-4">
        <Note kind="info">
          <strong>Why hybrid?</strong> A pure ML model extrapolates badly at the tails, and the
          tails are exactly where a polar station gets into trouble — storm cut-out, rime icing,
          polar night. The physics baseline keeps the prediction anchored; the ML layer corrects
          its systematic error. Both parts are reported separately below.
        </Note>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={reload}
          hint="Forecasts appear after the first successful refresh." />
      ) : loading ? (
        <Card><Loading rows={5} /></Card>
      ) : !data?.length ? (
        <Card><Empty message="No renewable forecast yet." hint="Press Refresh in the header." /></Card>
      ) : (
        <>
          <div className="grid grid-6 mb-4">
            <Card bodyClass="stat-card">
              <Stat label="Wind energy" value={num(stats.windEnergy, 0)} unit="kWh" size="sm"
                icon={<Wind size={11} />} sub={`over ${hours} h`} />
            </Card>
            <Card bodyClass="stat-card">
              <Stat label="Solar energy" value={num(stats.solarEnergy, 0)} unit="kWh" size="sm"
                icon={<Sun size={11} />} sub={`over ${hours} h`} />
            </Card>
            <Card bodyClass="stat-card">
              <Stat label="Peak output" value={num(stats.peak, 1)} unit="kW" size="sm" />
            </Card>
            <Card bodyClass="stat-card">
              <Stat label="Mean output" value={num(stats.mean, 1)} unit="kW" size="sm" />
            </Card>
            <Card bodyClass="stat-card">
              <Stat label="Icing-risk hours" value={stats.icingHours} unit="h" size="sm"
                icon={<Snowflake size={11} />} tone={stats.icingHours > 0 ? 'warn' : undefined}
                sub="rime accretion window" />
            </Card>
            <Card bodyClass="stat-card">
              <Stat label="Storm cut-out" value={stats.curtailHours} unit="h" size="sm"
                tone={stats.curtailHours > 0 ? 'crit' : undefined}
                sub="turbines feathered" />
            </Card>
          </div>

          <div className="grid grid-2 mb-4">
            <Card title="Predicted renewable output" icon={<Zap size={14} />}
              tag={<DataTag kind="AI_FORECAST" />}
              actions={
                <div className="seg-control">
                  {HORIZONS.map((o) => (
                    <button key={o.h} className={hours === o.h ? 'active' : ''}
                      onClick={() => setHours(o.h)}>{o.label}</button>
                  ))}
                </div>
              }>
              <ForecastBandChart
                data={chart} height={290} color="#2e9e7e" name="Total renewable"
                extraLines={[
                  { key: 'physical', name: 'Physics baseline', color: '#5c706c', dash: '4 3' },
                ]}
              />
              <Legend2 items={[
                { label: 'Hybrid prediction', color: '#2e9e7e' },
                { label: '80% interval', color: 'rgba(46,158,126,0.2)' },
                { label: 'Physics-only baseline', color: '#5c706c' },
              ]} />
            </Card>

            <Card title="Wind / solar split" icon={<Wind size={14} />}
              tag={<DataTag kind="AI_FORECAST" />}>
              <GenerationMixChart data={chart} height={290} showLoad={false} />
              <Legend2 items={[
                { label: 'Wind', color: '#1264a3' },
                { label: 'Solar', color: '#d99400' },
              ]} />
            </Card>
          </div>

          <Card title="Hourly renewable forecast" icon={<Sun size={14} />} bodyClass="flush">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Target (UTC)</th>
                    <th className="num">Wind</th>
                    <th className="num">Solar</th>
                    <th className="num">Total</th>
                    <th className="num">Physics</th>
                    <th className="num">ML corr.</th>
                    <th className="num">Wind in</th>
                    <th className="num">Solar in</th>
                    <th>Flags</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((r, i) => (
                    <tr key={i}>
                      <td className="mono tiny">{utc(r.target_time)}</td>
                      <td className="num" style={{ color: 'var(--series-wind)' }}>{num(r.wind_kw, 1)}</td>
                      <td className="num" style={{ color: 'var(--series-solar)' }}>{num(r.solar_kw, 1)}</td>
                      <td className="num strong">{num(r.total_kw, 1)}</td>
                      <td className="num faint">
                        {num((r.wind_physical_kw ?? 0) + (r.solar_physical_kw ?? 0), 1)}
                      </td>
                      <td className="num faint">{num(r.ml_correction_kw, 2)}</td>
                      <td className="num tiny">{num(r.input_wind_speed_ms, 1)} m/s</td>
                      <td className="num tiny">
                        {r.input_solar_radiation_wm2 != null ? `${num(r.input_solar_radiation_wm2, 0)} W/m²` : '—'}
                      </td>
                      <td>
                        <span className="row gap-2">
                          {r.turbine_curtailed && <Tag kind="crit">CUT-OUT</Tag>}
                          {r.icing_risk && <Tag kind="warn">ICING</Tag>}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
