import { Database, FlaskConical, MapPin, RefreshCw, Satellite, Server } from 'lucide-react'
import { usePolaris, useEndpoint } from '../hooks/usePolaris'
import api from '../services/api'
import {
  Card, DataTag, ErrorState, KV, Loading, Note, Stat, Tag,
} from '../components/Primitives'
import { int, num, utc } from '../utils/format'
import { useState } from 'react'

export default function DataModel() {
  const { tick } = usePolaris()
  const info = useEndpoint(() => api.modelInfo(), [tick])
  const providers = useEndpoint(() => api.providers(), [])
  const stations = useEndpoint(() => api.stations(), [])
  const [retraining, setRetraining] = useState(false)
  const [retrainMsg, setRetrainMsg] = useState(null)

  async function retrain() {
    setRetraining(true)
    setRetrainMsg(null)
    try {
      const res = await api.retrain()
      setRetrainMsg(`Retrained on ${res.rows} real observations.`)
      info.reload()
    } catch (err) {
      setRetrainMsg(err.message)
    } finally {
      setRetraining(false)
    }
  }

  const d = info.data

  return (
    <div className="page">
      <div className="page-intro">
        <h2>Data provenance &amp; model cards</h2>
        <p>
          Exactly what is real, what is modelled, and where every number comes from. This page
          exists so the project&apos;s data-honesty claims can be audited rather than trusted.
        </p>
      </div>

      <div className="grid grid-2 mb-4">
        <Note kind="info">
          <strong>REAL data — weather only.</strong> Temperature, wind speed and direction,
          humidity, solar radiation, pressure, cloud cover and present weather are measured
          observations retrieved from external meteorological providers.
        </Note>
        <Note kind="model">
          <strong>MODELLED — everything about the energy system.</strong> Station load, battery
          state of charge and capacity, fuel level, generator state, critical-load demand and
          energy autonomy are all research-based model estimates. No Indian Antarctic station
          publishes a public real-time electrical telemetry feed.
        </Note>
      </div>

      {info.error ? (
        <ErrorState error={info.error} onRetry={info.reload} />
      ) : info.loading ? (
        <Card><Loading rows={5} /></Card>
      ) : d ? (
        <>
          <div className="grid grid-2-1 mb-4">
            <Card title="Station under management" icon={<MapPin size={14} />}
              tag={<DataTag kind="REAL_LIVE_WEATHER" />}>
              <div className="grid grid-2">
                <div>
                  <KV k="Station" v={d.station.station_name} />
                  <KV k="Operator" v={d.station.operator} />
                  <KV k="WMO index" v={d.station.wmo_index} />
                  <KV k="Region" v={d.station.region} />
                  <KV k="Latitude" v={`${num(d.station.latitude, 4)}°`} />
                  <KV k="Longitude" v={`${num(d.station.longitude, 4)}°`} />
                  <KV k="Elevation" v={`${int(d.station.elevation_m)} m`} />
                </div>
                <div>
                  <div className="tiny upper muted mb-2">Modelled plant nameplate</div>
                  <KV k="Wind" v={`${d.station.wind_turbines} × ${num(d.station.wind_rated_kw / d.station.wind_turbines, 0)} kW = ${int(d.station.wind_rated_kw)} kW`} />
                  <KV k="Solar PV" v={`${int(d.station.solar_rated_kwp)} kWp`} />
                  <KV k="Battery" v={`${int(d.station.battery_nominal_kwh)} kWh (${int(d.station.battery_usable_kwh)} usable)`} />
                  <KV k="Generators" v={`${d.station.generator_units} × ${num(d.station.generator_rated_kw / d.station.generator_units, 0)} kW`} />
                  <KV k="Fuel tank" v={`${int(d.station.fuel_capacity_l)} L`} />
                  <KV k="Fuel energy" v={`${num(d.station.fuel_energy_kwh_per_l, 2)} kWh/L`} />
                  <KV k="Crew" v={`${d.station.winter_crew} winter / ${d.station.summer_crew} summer`} />
                </div>
              </div>
            </Card>

            <Card title="Data coverage" icon={<Database size={14} />}>
              <Stat label="Total observations stored" value={int(d.data_coverage.total_observations)}
                sub="in PostgreSQL" size="sm" />
              <div className="mt-3">
                {d.data_coverage.by_provider.map((p) => (
                  <div key={p.provider} style={{ padding: '7px 0', borderBottom: '1px solid var(--pol-line-soft)' }}>
                    <div className="row-between gap-2">
                      <span className="small strong mono">{p.provider}</span>
                      <span className="num small">{int(p.count)}</span>
                    </div>
                    <div className="tiny muted">
                      {p.first ? utc(p.first) : '—'} → {p.last ? utc(p.last) : '—'}
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <div className="grid grid-2 mb-4">
            <Card title="Live data sources" icon={<Satellite size={14} />}>
              {Object.entries(d.providers || {}).map(([key, p]) => (
                <div key={key} style={{ padding: '10px 0', borderBottom: '1px solid var(--pol-line-soft)' }}>
                  <div className="row-between gap-2 mb-2">
                    <span className="strong small">{p.label}</span>
                    {p.authoritative && <Tag kind="ok">AUTHORITATIVE</Tag>}
                  </div>
                  <KV k="Type" v={p.kind} />
                  <KV k="Station" v={p.station} />
                  <KV k="Cadence" v={p.cadence} />
                  {(p.missing || []).length > 0 && (
                    <div className="tiny mt-2" style={{ color: 'var(--pol-warn)' }}>
                      Cannot report: {p.missing.join('; ')}
                    </div>
                  )}
                </div>
              ))}
              {providers.data?.note && (
                <div className="mt-3"><Note kind="info">{providers.data.note}</Note></div>
              )}
            </Card>

            <Card title="Model registry" icon={<FlaskConical size={14} />}
              actions={
                <button className="btn sm" onClick={retrain} disabled={retraining}>
                  <RefreshCw size={12} className={retraining ? 'spin' : ''} /> Retrain
                </button>
              }>
              <KV k="Trained" v={d.models.is_trained ? 'Yes' : 'No'} />
              <KV k="Trained at" v={d.models.trained_at ? utc(d.models.trained_at) : '—'} />
              <KV k="Training rows" v={int(d.models.training_rows)} />
              {d.models.last_error && (
                <div className="tiny mt-2" style={{ color: 'var(--pol-crit)' }}>
                  {d.models.last_error}
                </div>
              )}
              {retrainMsg && <div className="tiny mt-2 muted">{retrainMsg}</div>}

              <div className="mt-3">
                {Object.entries(d.models.reports || {}).map(([name, rep]) => (
                  rep && Object.keys(rep).length > 0 && (
                    <div key={name} style={{ padding: '9px 0', borderTop: '1px solid var(--pol-line-soft)' }}>
                      <div className="strong small mb-2">{name.replace(/_/g, ' ')}</div>
                      <KV k="Algorithm" v={rep.model_type} />
                      <KV k="Version" v={rep.model_version} />
                      <KV k="Samples" v={int(rep.n_samples)} />
                      {rep.mae_kw != null && <KV k="MAE" v={`${num(rep.mae_kw, 3)} kW`} />}
                      {rep.r2 != null && <KV k="R²" v={num(rep.r2, 4)} />}
                      {rep.wind_r2 != null && <KV k="Wind R²" v={num(rep.wind_r2, 4)} />}
                      {rep.contamination != null && <KV k="Contamination" v={num(rep.contamination, 3)} />}
                    </div>
                  )
                ))}
              </div>
            </Card>
          </div>

          <Card title="Modelled load channels" icon={<Server size={14} />} bodyClass="flush"
            className="mb-4">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Circuit</th>
                    <th>Priority</th>
                    <th className="num">Base kW</th>
                    <th className="num">Diurnal kW</th>
                    <th className="num">kW/°C</th>
                    <th className="num">Max shed</th>
                    <th>Description</th>
                  </tr>
                </thead>
                <tbody>
                  {(d.load_channels || []).map((c) => (
                    <tr key={c.key}>
                      <td className="strong">{c.label}</td>
                      <td>
                        <span className="row gap-2">
                          <span className={`pri-chip pri-${c.priority}`} />
                          <span className="tiny">{c.priority.split('_')[0]}</span>
                        </span>
                      </td>
                      <td className="num">{num(c.base_kw, 1)}</td>
                      <td className="num">{num(c.diurnal_kw, 1)}</td>
                      <td className="num">{c.hdd_kw_per_c ? num(c.hdd_kw_per_c, 2) : '—'}</td>
                      <td className="num">{num(c.shed_fraction_max * 100, 0)}%</td>
                      <td className="tiny muted" style={{ maxWidth: 380 }}>{c.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="grid grid-2">
            <Card title="Recent ingestion runs" icon={<RefreshCw size={14} />} bodyClass="flush">
              <div className="table-wrap" style={{ maxHeight: 300 }}>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Started (UTC)</th>
                      <th>Trigger</th>
                      <th>Provider</th>
                      <th className="num">Written</th>
                      <th className="num">ms</th>
                      <th>Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(d.recent_ingestion_runs || []).map((r, i) => (
                      <tr key={i}>
                        <td className="mono tiny">{utc(r.started_at)}</td>
                        <td className="tiny">{r.trigger}</td>
                        <td className="tiny mono">{r.provider_key || '—'}</td>
                        <td className="num">{int(r.written)}</td>
                        <td className="num tiny">{num(r.duration_ms, 0)}</td>
                        <td>
                          {r.succeeded ? <Tag kind="ok">OK</Tag> : <Tag kind="crit">FAILED</Tag>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card title="Registered stations" icon={<MapPin size={14} />} bodyClass="flush">
              <table className="table">
                <thead>
                  <tr>
                    <th>Code</th>
                    <th>Name</th>
                    <th>Operator</th>
                    <th>Kind</th>
                  </tr>
                </thead>
                <tbody>
                  {(stations.data || []).map((s) => (
                    <tr key={s.id}>
                      <td className="mono strong">{s.code}</td>
                      <td>{s.name}</td>
                      <td className="tiny muted">{s.operator}</td>
                      <td>
                        <Tag kind={s.kind === 'MODELLED_PLANT' ? 'modelled' : 'real'}>
                          {s.kind === 'MODELLED_PLANT' ? 'PLANT MODEL' : 'REFERENCE'}
                        </Tag>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>

          <div className="mt-4">
            <Card title="Operating thresholds" icon={<Server size={14} />}>
              <div className="grid grid-4">
                <KV k="Autonomy critical" v={`${d.thresholds.autonomy_critical_h} h`} />
                <KV k="Autonomy warning" v={`${d.thresholds.autonomy_warning_h} h`} />
                <KV k="SoC critical" v={`${d.thresholds.soc_critical_pct}%`} />
                <KV k="SoC warning" v={`${d.thresholds.soc_warning_pct}%`} />
                <KV k="Storm wind" v={`${d.thresholds.storm_wind_ms} m/s`} />
                <KV k="Extreme cold" v={`${d.thresholds.extreme_cold_c} °C`} />
                <KV k="Renewable target" v={`${num(d.thresholds.renewable_fraction_target * 100, 0)}%`} />
              </div>
            </Card>
          </div>
        </>
      ) : null}
    </div>
  )
}
