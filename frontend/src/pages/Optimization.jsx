import { useMemo, useState } from 'react'
import { Fuel, Leaf, Play, Sliders, TrendingDown } from 'lucide-react'
import { usePolaris, useEndpoint } from '../hooks/usePolaris'
import api from '../services/api'
import {
  Card, DataTag, Empty, ErrorState, KV, Loading, Stat, Tag,
} from '../components/Primitives'
import { GenerationMixChart, SocChart, Legend2 } from '../charts/Charts'
import { int, num, pct, utc } from '../utils/format'

export default function Optimization() {
  const { tick } = usePolaris()
  const [horizon, setHorizon] = useState(48)
  const [allowDeferral, setAllowDeferral] = useState(true)
  const [reserve, setReserve] = useState(30)
  const [gens, setGens] = useState(3)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [runError, setRunError] = useState(null)

  const latest = useEndpoint(() => api.latestOptimization(), [tick])
  const active = result || latest.data

  async function run() {
    setRunning(true)
    setRunError(null)
    try {
      const res = await api.runOptimization({
        horizon_h: horizon,
        allow_deferral: allowDeferral,
        reserve_soc_pct: reserve,
        generators_available: gens,
      })
      setResult(res.data)
    } catch (err) {
      setRunError(err.message)
    } finally {
      setRunning(false)
    }
  }

  const chart = useMemo(() => (active?.schedule || []).map((h) => ({
    time: h.timestamp,
    wind: h.wind_kw,
    solar: h.solar_kw,
    generator: h.generator_kw,
    load: h.load_kw,
    critical: h.critical_kw,
    soc: h.battery_soc_pct,
  })), [active])

  return (
    <div className="page">
      <div className="page-intro">
        <h2>Energy optimisation</h2>
        <p>
          Receding-horizon dispatch that minimises diesel consumption while guaranteeing
          life-critical supply. Deferrable load (snow melting, vehicle charging, batch lab work)
          is shifted into renewable-surplus hours.
        </p>
      </div>

      <div className="grid grid-1-2 mb-4">
        <Card title="Optimiser parameters" icon={<Sliders size={14} />}>
          <div className="field mb-3">
            <label>Planning horizon</label>
            <div className="seg-control">
              {[24, 48, 72].map((h) => (
                <button key={h} className={horizon === h ? 'active' : ''}
                  onClick={() => setHorizon(h)}>{h} h</button>
              ))}
            </div>
          </div>

          <div className="field mb-3">
            <label>Battery reserve floor · {reserve}%</label>
            <input type="range" min={10} max={60} step={5} value={reserve}
              onChange={(e) => setReserve(Number(e.target.value))} />
            <span className="tiny muted">
              The optimiser will not discharge below this except for critical load.
            </span>
          </div>

          <div className="field mb-3">
            <label>Generators available</label>
            <div className="seg-control">
              {[0, 1, 2, 3].map((g) => (
                <button key={g} className={gens === g ? 'active' : ''}
                  onClick={() => setGens(g)}>{g}</button>
              ))}
            </div>
          </div>

          <div className="field mb-4">
            <label>Deferrable load scheduling</label>
            <div className="seg-control">
              <button className={allowDeferral ? 'active' : ''} onClick={() => setAllowDeferral(true)}>
                Enabled
              </button>
              <button className={!allowDeferral ? 'active' : ''} onClick={() => setAllowDeferral(false)}>
                Disabled
              </button>
            </div>
          </div>

          <button className="btn primary" onClick={run} disabled={running} style={{ width: '100%' }}>
            <Play size={14} className={running ? 'spin' : ''} />
            {running ? 'Optimising…' : 'Run optimisation'}
          </button>

          {runError && <div className="mt-3"><ErrorState error={runError} /></div>}
        </Card>

        <Card title="Result" icon={<TrendingDown size={14} />}
          tag={<DataTag kind="MODELLED_ENERGY" />}
          footer={active && (
            <div className="row-between wrap gap-3">
              <span>Run {utc(active.run_at)} · solved in {num(active.solve_ms, 0)} ms</span>
              <span>{active.feasible
                ? <Tag kind="ok">FEASIBLE</Tag>
                : <Tag kind="crit">INFEASIBLE</Tag>}</span>
            </div>
          )}>
          {latest.loading && !result ? <Loading rows={4} />
            : !active ? (
              <Empty message="No optimisation run yet."
                hint="Set the parameters and press Run optimisation." />
            ) : (
              <>
                <div className="grid grid-3 mb-3">
                  <Stat label="Fuel saved" value={num(active.fuel_saved_l, 1)} unit="L"
                    tone={active.fuel_saved_l > 0 ? 'ok' : undefined}
                    sub={`${pct(active.fuel_saved_pct, 2)} vs baseline`} icon={<Fuel size={11} />} />
                  <Stat label="CO₂ avoided" value={num(active.co2_avoided_kg, 0)} unit="kg"
                    icon={<Leaf size={11} />} sub="at 2.68 kg/L" />
                  <Stat label="Renewable share" value={pct(active.renewable_fraction * 100, 1)}
                    sub="of served energy" />
                </div>
                <div className="grid grid-2">
                  <div>
                    <KV k="Baseline burn" v={`${num(active.baseline_fuel_l, 1)} L`} />
                    <KV k="Optimised burn" v={`${num(active.optimized_fuel_l, 1)} L`} />
                    <KV k="Load deferred" v={`${num(active.load_deferred_kwh, 1)} kWh`} />
                    <KV k="Load shed" v={`${num(active.load_shed_kwh, 1)} kWh`} />
                  </div>
                  <div>
                    <KV k="Generator runtime" v={`${num(active.generator_runtime_h, 0)} h`} />
                    <KV k="Generator starts" v={int(active.generator_starts)} />
                    <KV k="Curtailed" v={`${num(active.renewable_curtailed_kwh, 1)} kWh`} />
                    <KV k="Final SoC" v={pct(active.final_soc_pct, 1)} />
                  </div>
                </div>
                {active.constraints_binding?.length > 0 && (
                  <div className="row gap-2 wrap mt-3">
                    <span className="tiny muted">Binding constraints:</span>
                    {active.constraints_binding.map((c) => (
                      <Tag kind="warn" key={c}>{c.replace(/_/g, ' ')}</Tag>
                    ))}
                  </div>
                )}
              </>
            )}
        </Card>
      </div>

      {active?.rationale?.length > 0 && (
        <Card title="Why this plan" icon={<Sliders size={14} />} className="mb-4">
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.75, fontSize: 12.5 }}>
            {active.rationale.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </Card>
      )}

      {chart.length > 0 && (
        <>
          <div className="grid grid-3-2 mb-4">
            <Card title="Optimised dispatch schedule" icon={<TrendingDown size={14} />}
              tag={<DataTag kind="MODELLED_ENERGY" />}>
              <GenerationMixChart data={chart} height={300} />
              <Legend2 items={[
                { label: 'Wind', color: '#1264a3' },
                { label: 'Solar', color: '#d99400' },
                { label: 'Diesel', color: '#a04a2f' },
                { label: 'Load', color: '#0b4f3f' },
                { label: 'Critical', color: '#b3261e' },
              ]} />
            </Card>
            <Card title="Planned battery trajectory" icon={<Sliders size={14} />}>
              <SocChart data={chart} height={300} socReserve={reserve} />
            </Card>
          </div>

          <Card title="Hour-by-hour dispatch" icon={<Sliders size={14} />} bodyClass="flush">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Hour (UTC)</th>
                    <th className="num">Load</th>
                    <th className="num">Defer orig</th>
                    <th className="num">Defer sched</th>
                    <th className="num">Wind</th>
                    <th className="num">Solar</th>
                    <th className="num">Battery</th>
                    <th className="num">SoC</th>
                    <th className="num">Diesel</th>
                    <th className="num">Fuel</th>
                    <th>Decision</th>
                  </tr>
                </thead>
                <tbody>
                  {active.schedule.map((h, i) => (
                    <tr key={i}>
                      <td className="mono tiny">{utc(h.timestamp)}</td>
                      <td className="num">{num(h.load_kw, 1)}</td>
                      <td className="num faint">{num(h.deferrable_original_kw, 1)}</td>
                      <td className="num" style={{
                        color: h.deferrable_scheduled_kw > h.deferrable_original_kw
                          ? 'var(--pol-ok)' : undefined,
                      }}>{num(h.deferrable_scheduled_kw, 1)}</td>
                      <td className="num" style={{ color: 'var(--series-wind)' }}>{num(h.wind_kw, 1)}</td>
                      <td className="num" style={{ color: 'var(--series-solar)' }}>{num(h.solar_kw, 1)}</td>
                      <td className="num">
                        {h.battery_charge_kw > 0.05 ? `+${num(h.battery_charge_kw, 1)}`
                          : h.battery_discharge_kw > 0.05 ? `−${num(h.battery_discharge_kw, 1)}` : '—'}
                      </td>
                      <td className="num">{num(h.battery_soc_pct, 1)}%</td>
                      <td className="num" style={{ color: 'var(--series-generator)' }}>
                        {num(h.generator_kw, 1)}
                      </td>
                      <td className="num tiny">{num(h.fuel_lph, 1)} L</td>
                      <td className="tiny muted" style={{ maxWidth: 320 }}>{h.reason}</td>
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
