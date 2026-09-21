import { useState } from 'react'
import { Fuel, ShieldCheck, Timer, Wind } from 'lucide-react'
import { usePolaris, useEndpoint } from '../hooks/usePolaris'
import api from '../services/api'
import {
  Card, DataTag, Empty, ErrorState, KV, Loading, Note, RiskBadge, Tag, Bar,
} from '../components/Primitives'
import { Gauge as GaugeChart } from '../charts/Charts'
import { duration, int, num, statusTag } from '../utils/format'

export default function Survival() {
  const { tick } = usePolaris()
  const [gens, setGens] = useState(null)
  const { data, loading, error, reload } = useEndpoint(
    () => api.survival(gens), [gens, tick],
  )

  return (
    <div className="page">
      <div className="page-intro">
        <h2>Energy survival analysis</h2>
        <p>
          How long the station can hold life-critical and science-critical load with the energy
          currently on hand. All inputs are modelled estimates driven by live weather.
        </p>
      </div>

      <div className="mb-4">
        <Note kind="model">
          <strong>What this answers.</strong> Given the modelled battery charge, fuel inventory
          and current renewable output, how many hours of critical supply remain? The battery is
          not discharged below its protected floor and an emergency fuel reserve is withheld —
          so this is a <em>usable</em> endurance figure, not a theoretical one.
        </Note>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={reload}
          hint="Available after the first model run." />
      ) : loading ? (
        <Card><Loading rows={5} /></Card>
      ) : !data ? (
        <Card><Empty message="No survival analysis yet." hint="Press Refresh in the header." /></Card>
      ) : (
        <>
          <div className="grid grid-4 mb-4">
            <Card className={`accent-${statusTag(data.status) === 'ok' ? 'green' : 'blue'}`}>
              <div className="row-between mb-2">
                <span className="stat-label"><Timer size={11} /> Estimated autonomy</span>
                <Tag kind={statusTag(data.status)}>{data.status}</Tag>
              </div>
              <div className="stat-value lg" style={{
                color: statusTag(data.status) === 'crit' ? 'var(--pol-crit)'
                  : statusTag(data.status) === 'warn' ? 'var(--pol-warn)' : 'var(--pol-ok)',
              }}>
                {data.estimated_autonomy_hours == null
                  ? 'Indefinite'
                  : duration(data.estimated_autonomy_hours)}
              </div>
              <div className="stat-sub mt-2">
                {data.estimated_autonomy_days != null && `${num(data.estimated_autonomy_days, 1)} days · `}
                limited by {data.limited_by}
              </div>
              {data.headroom_vs_threshold_h != null && (
                <div className="tiny muted mt-2">
                  {data.headroom_vs_threshold_h >= 0
                    ? `${duration(data.headroom_vs_threshold_h)} above the 72 h planning reserve`
                    : `${duration(Math.abs(data.headroom_vs_threshold_h))} below the 72 h planning reserve`}
                </div>
              )}
            </Card>

            <Card>
              <GaugeChart value={data.battery_soc_pct} label="Battery SoC" color="#14a0b4" height={130} />
              <KV k="Battery endurance" v={duration(data.battery_hours)} />
              <KV k="Available energy" v={`${num(data.battery_available_kwh, 1)} kWh`} />
            </Card>

            <Card>
              <GaugeChart value={data.fuel_level_pct} label="Fuel level" color="#6b5b95" height={130} />
              <KV k="Fuel endurance" v={duration(data.fuel_hours)} />
              <KV k="Usable fuel" v={`${int(data.fuel_usable_l)} L`} />
            </Card>

            <Card>
              <div className="stat mb-3">
                <span className="stat-label"><Wind size={11} /> Demand vs renewable</span>
                <span className="stat-value sm">{num(data.critical_demand_kw, 1)}<span className="stat-unit">kW critical</span></span>
              </div>
              <KV k="Total demand" v={`${num(data.total_demand_kw, 1)} kW`} />
              <KV k="Renewable now" v={`${num(data.renewable_generation_kw, 1)} kW`} />
              <KV k="Generators" v={`${data.generators_available} × ${num(data.generator_capacity_kw / Math.max(1, data.generators_available), 0)} kW`} />
              <div className="mt-3">
                <div className="tiny muted mb-2">Renewable coverage of critical load</div>
                <Bar value={Math.min(100, (data.renewable_generation_kw / Math.max(1, data.critical_demand_kw)) * 100)}
                  color="var(--series-renewable)" />
              </div>
            </Card>
          </div>

          {data.modes && (
            <Card title="Autonomy by operating posture" icon={<Timer size={14} />}
              tag={<DataTag kind="MODELLED_ENERGY" />} className="mb-4"
              footer="Crisis assumes critical load only with zero renewable input — turbines feathered and no sun. That is the figure the emergency reserve should be sized against.">
              <div className="grid grid-3">
                {[data.modes.normal, data.modes.critical_only, data.modes.crisis].map((m) => (
                  <div key={m.label} style={{
                    padding: '12px 14px', borderRadius: 'var(--r-md)',
                    border: '1px solid var(--pol-line)', background: 'var(--pol-surface)',
                  }}>
                    <div className="tiny upper muted mb-2">{m.label}</div>
                    <div className="stat-value" style={{ fontSize: 22 }}>
                      {m.hours == null ? 'Indefinite' : duration(m.hours)}
                    </div>
                    <div className="tiny muted mt-2" style={{ lineHeight: 1.5 }}>
                      {m.description}
                    </div>
                    <div className="mt-2">
                      <KV k="Demand held" v={`${num(m.demand_kw, 1)} kW`} />
                      <KV k="Renewable offset" v={`${num(m.renewable_kw, 1)} kW`} />
                      <KV k="Limited by" v={m.limited_by} />
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {data.risk && (
            <Card title="Operational risk assessment" icon={<ShieldCheck size={14} />}
              tag={<DataTag kind="MODELLED_ENERGY" />} className="mb-4"
              actions={<RiskBadge level={data.risk.level} scorePct={data.risk.score_pct}
                color={data.risk.color} />}>
              <div className="small mb-3" style={{ lineHeight: 1.6 }}>{data.risk.headline}</div>
              <div className="risk-meter mb-4">
                <div className="risk-meter-fill" style={{
                  width: `${data.risk.score_pct}%`, background: data.risk.color,
                }} />
              </div>
              {data.risk.factors.map((f) => (
                <div className="factor-row" key={f.key}>
                  <div>
                    <div className="small strong">{f.label}</div>
                    <div className="tiny muted">{f.detail}</div>
                    <div className="factor-bar">
                      <div className="factor-bar-fill" style={{
                        width: `${f.score * 100}%`,
                        background: f.score > 0.66 ? 'var(--pol-crit)'
                          : f.score > 0.33 ? 'var(--pol-warn)' : 'var(--pol-ok)',
                      }} />
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="num small strong">{f.value}</div>
                    <div className="tiny faint">weight {num(f.weight * 100, 0)}%</div>
                  </div>
                </div>
              ))}
            </Card>
          )}

          <div className="grid grid-2-1">
            <Card title="Energy reserve breakdown" icon={<ShieldCheck size={14} />}
              tag={<DataTag kind="MODELLED_ENERGY" />} bodyClass="flush">
              <table className="table">
                <thead>
                  <tr>
                    <th>Source</th>
                    <th className="num">Available</th>
                    <th className="num">Endurance</th>
                    <th>Basis</th>
                  </tr>
                </thead>
                <tbody>
                  {data.breakdown.map((b, i) => (
                    <tr key={i}>
                      <td className="strong">{b.source}</td>
                      <td className="num">{num(b.available, 1)} {b.unit}</td>
                      <td className="num">{b.hours != null ? duration(b.hours) : '—'}</td>
                      <td className="tiny muted">{b.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <Card title="Scenario controls" icon={<Fuel size={14} />}>
              <div className="field mb-3">
                <label>Generators assumed available</label>
                <div className="seg-control">
                  <button className={gens === null ? 'active' : ''} onClick={() => setGens(null)}>
                    All
                  </button>
                  {[2, 1, 0].map((g) => (
                    <button key={g} className={gens === g ? 'active' : ''} onClick={() => setGens(g)}>
                      {g}
                    </button>
                  ))}
                </div>
                <span className="tiny muted">
                  Recompute endurance assuming some units are down. With zero generators, only
                  the battery carries the station.
                </span>
              </div>

              <div className="mt-4">
                <div className="tiny upper muted mb-2">Assumptions</div>
                <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11.5, lineHeight: 1.7, color: 'var(--pol-muted)' }}>
                  {data.assumptions.map((a, i) => <li key={i}>{a}</li>)}
                </ul>
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
