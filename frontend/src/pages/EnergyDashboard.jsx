import { useMemo, useState } from 'react'
import {
  Activity, BatteryCharging, Fuel, Gauge, Layers, Zap,
} from 'lucide-react'
import { usePolaris, useEndpoint } from '../hooks/usePolaris'
import api from '../services/api'
import {
  Card, DataTag, Empty, ErrorState, KV, Loading, Note, StackBar, Tag,
} from '../components/Primitives'
import { GenerationMixChart, SocChart, PriorityBarChart, Gauge as GaugeChart } from '../charts/Charts'
import { int, num, pct, PRIORITY_LABEL, utc } from '../utils/format'

const RANGES = [
  { label: '24 h', hours: 24 },
  { label: '48 h', hours: 48 },
  { label: '72 h', hours: 72 },
  { label: '7 d', hours: 168 },
]

const PRIORITY_COLOR = {
  P1_LIFE_CRITICAL: '#0b4f3f',
  P2_SCIENCE_CRITICAL: '#1264a3',
  P3_OPERATIONAL: '#2e9e7e',
  P4_DEFERRABLE: '#7fd4e2',
}

export default function EnergyDashboard() {
  const { tick } = usePolaris()
  const [hours, setHours] = useState(48)

  const status = useEndpoint(() => api.energyStatus(), [tick])
  const history = useEndpoint(() => api.energyHistory(hours), [hours, tick])
  const profile = useEndpoint(() => api.loadProfile(), [tick])

  const chartData = useMemo(() => (history.data || []).map((r) => ({
    time: r.recorded_at,
    wind: r.wind_generation_kw,
    solar: r.solar_generation_kw,
    generator: r.generator_output_kw,
    load: r.total_load_kw,
    critical: r.critical_load_kw,
    soc: r.battery_soc_pct,
    fuel: r.fuel_level_l,
  })), [history.data])

  const priorityData = useMemo(() => {
    const rows = profile.data || []
    const byPriority = {}
    rows.forEach((r) => {
      byPriority[r.priority] = (byPriority[r.priority] || 0) + r.demand_kw
    })
    return Object.entries(byPriority)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => ({
        name: PRIORITY_LABEL[k] || k,
        value: Number(v.toFixed(2)),
        color: PRIORITY_COLOR[k] || '#888',
      }))
  }, [profile.data])

  const e = status.data

  return (
    <div className="page">
      <div className="page-intro">
        <h2>Modelled station energy balance</h2>
        <p>
          Generation, storage, fuel and demand estimated from live Antarctic weather by the
          POLARIS research-based energy model. These are engineering estimates, not measured
          station telemetry.
        </p>
      </div>

      <div className="mb-4">
        <Note kind="model">
          <strong>MODELLED — not telemetry.</strong> Battery state of charge, fuel level,
          generator state and station load are computed by a physics model whose only real
          inputs are the measured weather variables. No Indian Antarctic station publishes
          real-time electrical telemetry.
        </Note>
      </div>

      {status.error ? (
        <ErrorState error={status.error} onRetry={status.reload}
          hint="Press Refresh in the header to pull live weather and run the model." />
      ) : status.loading ? (
        <Card><Loading rows={4} /></Card>
      ) : e ? (
        <>
          <div className="grid grid-4 mb-4">
            <Card className="accent-cyan">
              <GaugeChart value={e.battery_soc_pct} label="Battery SoC"
                color={e.battery_soc_pct <= 20 ? '#b3261e' : e.battery_soc_pct <= 35 ? '#9a6200' : '#14a0b4'} />
              <KV k="Stored" v={`${int(e.battery_stored_kwh)} kWh`} />
              <KV k="Mode" v={e.battery_mode} />
              <KV k="Charge / discharge"
                v={`${num(e.battery_charge_kw, 1)} / ${num(e.battery_discharge_kw, 1)} kW`} />
            </Card>

            <Card className="accent-green">
              <GaugeChart value={e.fuel_level_pct} label="Fuel level"
                color={e.fuel_level_pct < 20 ? '#b3261e' : '#6b5b95'} />
              <KV k="Volume" v={`${int(e.fuel_level_l)} L`} />
              <KV k="Burn rate" v={`${num(e.fuel_consumed_l, 2)} L/h`} />
              <KV k="CO₂ this hour" v={`${num(e.co2_kg, 1)} kg`} />
            </Card>

            <Card className="accent-blue">
              <GaugeChart value={e.renewable_fraction * 100} label="Renewable share" color="#2e9e7e" />
              <KV k="Wind" v={`${num(e.wind_generation_kw, 1)} kW`} />
              <KV k="Solar" v={`${num(e.solar_generation_kw, 1)} kW`} />
              <KV k="Curtailed" v={`${num(e.renewable_curtailed_kw, 1)} kW`} />
            </Card>

            <Card>
              <div className="stat mb-3">
                <span className="stat-label"><Zap size={11} /> Demand vs supply</span>
                <span className="stat-value">{num(e.total_load_kw, 1)}<span className="stat-unit">kW</span></span>
                <span className="stat-sub">{num(e.critical_load_kw, 1)} kW critical</span>
              </div>
              <div className="mb-2">
                <div className="tiny muted mb-2">Supply mix</div>
                <StackBar segments={[
                  { label: 'Wind', value: e.wind_generation_kw, color: 'var(--series-wind)' },
                  { label: 'Solar', value: e.solar_generation_kw, color: 'var(--series-solar)' },
                  { label: 'Battery', value: e.battery_discharge_kw, color: 'var(--series-battery)' },
                  { label: 'Diesel', value: e.generator_output_kw, color: 'var(--series-generator)' },
                ]} />
              </div>
              <KV k="Generator" v={e.generators_running > 0
                ? `${num(e.generator_output_kw, 1)} kW @ ${num(e.generator_loading_pct, 0)}%`
                : 'Offline'} />
              <KV k="Shed load" v={`${num(e.shed_load_kw, 1)} kW`} />
              {e.wet_stacking && (
                <div className="mt-2"><Tag kind="warn">WET-STACKING RISK</Tag></div>
              )}
            </Card>
          </div>

          <div className="grid grid-3-2 mb-4">
            <Card
              title={`Generation & Demand · last ${hours} h`}
              icon={<Layers size={14} />}
              tag={<DataTag kind="MODELLED_ENERGY" />}
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
                : chartData.length ? <GenerationMixChart data={chartData} height={300} />
                  : <Empty message="No history yet." />}
            </Card>

            <Card title="Load by Priority" icon={<Activity size={14} />}
              tag={<DataTag kind="MODELLED_ENERGY" />}>
              {profile.loading ? <Loading rows={3} />
                : priorityData.length ? (
                  <>
                    <PriorityBarChart data={priorityData} height={190} />
                    <div className="tiny muted mt-3">
                      Shedding walks the ladder upward from P4. P1 life-critical circuits are
                      never shed.
                    </div>
                  </>
                ) : <Empty message="No load profile yet." />}
            </Card>
          </div>

          <div className="grid grid-2 mb-4">
            <Card title="Battery State of Charge" icon={<BatteryCharging size={14} />}
              tag={<DataTag kind="MODELLED_ENERGY" />}>
              {chartData.length ? <SocChart data={chartData} height={230} />
                : <Empty message="No history yet." />}
            </Card>

            <Card title="Circuit Breakdown" icon={<Gauge size={14} />}
              bodyClass="flush" tag={<DataTag kind="MODELLED_ENERGY" />}>
              <div className="table-wrap" style={{ maxHeight: 268 }}>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Circuit</th>
                      <th>Priority</th>
                      <th className="num">Demand</th>
                      <th>Type</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(profile.data || []).map((r) => (
                      <tr key={r.channel_key}>
                        <td>{r.channel_label}</td>
                        <td>
                          <span className="row gap-2">
                            <span className={`pri-chip pri-${r.priority}`} />
                            <span className="tiny">{r.priority.split('_')[0]}</span>
                          </span>
                        </td>
                        <td className="num">{num(r.demand_kw, 2)} kW</td>
                        <td>
                          {r.is_deferrable
                            ? <Tag kind="info">DEFERRABLE</Tag>
                            : <span className="tiny muted">fixed</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <Card title="Model state detail" icon={<Fuel size={14} />}>
            <div className="grid grid-4">
              <div>
                <div className="tiny upper muted mb-2">Demand</div>
                <KV k="Total load" v={`${num(e.total_load_kw, 2)} kW`} />
                <KV k="Critical" v={`${num(e.critical_load_kw, 2)} kW`} />
                <KV k="Deferrable" v={`${num(e.deferrable_load_kw, 2)} kW`} />
                <KV k="Shed" v={`${num(e.shed_load_kw, 2)} kW`} />
              </div>
              <div>
                <div className="tiny upper muted mb-2">Generation</div>
                <KV k="Wind" v={`${num(e.wind_generation_kw, 2)} kW`} />
                <KV k="Solar" v={`${num(e.solar_generation_kw, 2)} kW`} />
                <KV k="Diesel" v={`${num(e.generator_output_kw, 2)} kW`} />
                <KV k="Curtailed" v={`${num(e.renewable_curtailed_kw, 2)} kW`} />
              </div>
              <div>
                <div className="tiny upper muted mb-2">Storage</div>
                <KV k="State of charge" v={pct(e.battery_soc_pct, 2)} />
                <KV k="Stored energy" v={`${int(e.battery_stored_kwh)} kWh`} />
                <KV k="Charging" v={`${num(e.battery_charge_kw, 2)} kW`} />
                <KV k="Discharging" v={`${num(e.battery_discharge_kw, 2)} kW`} />
              </div>
              <div>
                <div className="tiny upper muted mb-2">Driving weather (REAL)</div>
                <KV k="Temperature" v={`${num(e.temperature_c, 1)} °C`} />
                <KV k="Wind speed" v={`${num(e.wind_speed_ms, 1)} m/s`} />
                <KV k="Model time" v={utc(e.timestamp)} />
                <KV k="Renewable fraction" v={pct(e.renewable_fraction * 100, 1)} />
              </div>
            </div>
          </Card>
        </>
      ) : (
        <Card><Empty message="No modelled energy state yet."
          hint="Press Refresh in the header to pull live weather and run the model." /></Card>
      )}
    </div>
  )
}
