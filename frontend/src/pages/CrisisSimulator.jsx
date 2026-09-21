import { useMemo, useState } from 'react'
import {
  AlertTriangle, ArrowRight, CloudSnow, Play, RotateCcw, ShieldAlert, Sun,
  Thermometer, Wind, Zap,
} from 'lucide-react'
import { useEndpoint, usePolaris } from '../hooks/usePolaris'
import api from '../services/api'
import {
  Card, DataTag, Empty, ErrorState, KV, Loading, RiskBadge, SimBanner, Stat,
  Tag, WhyThisAction,
} from '../components/Primitives'
import { CrisisTimelineChart, Legend2 } from '../charts/Charts'
import { duration, int, num, ratingTag, riskTag, utc } from '../utils/format'

/** Slider definitions. `null` means "use the scenario preset". */
const SLIDERS = [
  { key: 'wind_speed_ms', label: 'Wind speed', unit: 'm/s', min: 0, max: 45,
    step: 0.5, icon: Wind,
    hint: 'Turbines cut out at 25 m/s' },
  { key: 'solar_radiation_wm2', label: 'Solar radiation', unit: 'W/m²', min: 0,
    max: 900, step: 10, icon: Sun,
    hint: 'Polar night is 0 W/m²' },
  { key: 'temperature_c', label: 'Air temperature', unit: '°C', min: -70,
    max: 5, step: 1, icon: Thermometer,
    hint: 'Drives heating demand' },
  { key: 'demand_multiplier', label: 'Demand', unit: '×', min: 0.4, max: 2.5,
    step: 0.05, icon: Zap,
    hint: '1.0 = seasonal norm' },
  { key: 'initial_soc_pct', label: 'Starting battery', unit: '%', min: 15,
    max: 95, step: 1, icon: null,
    hint: 'Protected floor is 15%' },
  { key: 'initial_fuel_l', label: 'Starting fuel', unit: 'L', min: 0,
    max: 80000, step: 1000, icon: null,
    hint: 'Emergency reserve is 12,000 L' },
  { key: 'generators_available', label: 'Generators available', unit: '', min: 0,
    max: 3, step: 1, icon: null,
    hint: '3 units of 100 kW' },
]

function deltaClass(after, before, lowerIsBetter = false) {
  const d = after - before
  if (Math.abs(d) < 0.05) return 'same'
  const worse = lowerIsBetter ? d > 0 : d < 0
  return worse ? 'worse' : 'better'
}

function Delta({ after, before, unit = '', digits = 1, lowerIsBetter = false }) {
  if (after == null || before == null) return null
  const d = after - before
  if (Math.abs(d) < 0.05) return <span className="ba-delta same">no change</span>
  return (
    <span className={`ba-delta ${deltaClass(after, before, lowerIsBetter)}`}>
      {d > 0 ? '+' : ''}{num(d, digits)}{unit}
    </span>
  )
}

export default function CrisisSimulator() {
  const { summary } = usePolaris()
  const scenarios = useEndpoint(() => api.scenarios(), [])

  const [selected, setSelected] = useState('SEVERE_STORM')
  const [durationH, setDurationH] = useState(72)
  const [severity, setSeverity] = useState(1.0)
  const [overrides, setOverrides] = useState({})
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const list = scenarios.data?.scenarios || []
  const current = list.find((s) => s.scenario === selected)

  // Slider defaults come from the live modelled state, so moving a slider is
  // always a deliberate departure from the real current value.
  const defaults = useMemo(() => {
    const live = summary?.energy
    const wx = summary?.live_weather?.fields || {}
    return {
      wind_speed_ms: wx.wind_speed_ms?.value ?? 8,
      solar_radiation_wm2: wx.solar_radiation_wm2?.value ?? 0,
      temperature_c: wx.temperature_c?.value ?? -20,
      demand_multiplier: 1.0,
      initial_soc_pct: live?.battery_soc_pct ?? 50,
      initial_fuel_l: live?.fuel_level_l ?? 40000,
      generators_available: 3,
    }
  }, [summary])

  const setOverride = (key, value) => setOverrides((o) => ({ ...o, [key]: value }))
  const clearOverrides = () => setOverrides({})

  async function run() {
    setRunning(true)
    setError(null)
    try {
      const res = await api.simulate({
        scenario: selected,
        duration_h: durationH,
        severity,
        ...overrides,
      })
      setResult(res.data)
    } catch (err) {
      setError(err.message)
    } finally {
      setRunning(false)
    }
  }

  const timeline = useMemo(() => (result?.timeline || []).map((t) => ({
    hour: t.hour,
    load: t.load_kw,
    renewable: t.renewable_kw,
    generator: t.generator_kw,
    soc: t.battery_soc_pct,
    shed: t.shed_kw,
  })), [result])

  const before = result?.before
  const after = result?.after

  return (
    <div className="page">
      <div className="page-intro">
        <h2>Polar crisis simulator</h2>
        <p>
          Stress-test the station against the failure modes that actually threaten polar power
          plants. Pick a scenario, adjust any input, and the full physics model re-runs against
          the real weather forecast.
        </p>
      </div>

      <div className="mb-4">
        <SimBanner>
          SIMULATED SCENARIO — NOT LIVE STATION TELEMETRY. Results are what-if projections and
          are never written to the live energy state.
        </SimBanner>
      </div>

      {/* ------------------------------------------------ scenario buttons */}
      <Card title="1 · Select scenario" icon={<ShieldAlert size={14} />} className="mb-4">
        {scenarios.loading ? <Loading rows={2} /> : (
          <>
            <div className="scenario-grid">
              {list.map((s) => {
                const Icon = {
                  NORMAL_OPERATION: Zap, SEVERE_STORM: CloudSnow,
                  LOW_SOLAR: Sun, WIND_FAILURE: Wind,
                  GENERATOR_FAILURE: AlertTriangle, HIGH_DEMAND: Zap,
                  COMBINED_CRISIS: ShieldAlert,
                }[s.scenario] || ShieldAlert
                return (
                  <button
                    key={s.scenario}
                    className={`scenario-btn ${selected === s.scenario ? 'active' : ''}`}
                    onClick={() => setSelected(s.scenario)}
                    aria-pressed={selected === s.scenario}
                  >
                    <span className="sb-name"><Icon size={13} /> {s.label}</span>
                    <span className="sb-note">{s.operator_note}</span>
                  </button>
                )
              })}
            </div>
            {current && (
              <div className="small muted mt-3" style={{ lineHeight: 1.65 }}>
                {current.description}
              </div>
            )}
          </>
        )}
      </Card>

      {/* ------------------------------------------------ inputs + run */}
      <div className="grid grid-1-2 mb-4">
        <Card
          title="2 · Adjust inputs"
          icon={<Wind size={14} />}
          actions={
            Object.keys(overrides).length > 0 && (
              <button className="btn sm" onClick={clearOverrides}>
                <RotateCcw size={12} /> Reset to live
              </button>
            )
          }
          footer="Untouched sliders keep the scenario preset. Any slider you move overrides it."
        >
          {SLIDERS.map((s) => {
            const isSet = overrides[s.key] !== undefined
            const value = isSet ? overrides[s.key] : defaults[s.key]
            const Icon = s.icon
            return (
              <div className={`slider-row ${isSet ? 'overridden' : ''}`} key={s.key}>
                <div className="slider-head">
                  <label htmlFor={`sl-${s.key}`}>
                    {Icon && <Icon size={10} style={{ marginRight: 4 }} />}{s.label}
                  </label>
                  <span className="slider-val">
                    {s.key === 'initial_fuel_l' ? int(value) : num(value, s.step < 1 ? 1 : 0)}
                    {s.unit && ` ${s.unit}`}
                  </span>
                </div>
                <input
                  id={`sl-${s.key}`} type="range"
                  min={s.min} max={s.max} step={s.step}
                  value={value}
                  onChange={(e) => setOverride(s.key, Number(e.target.value))}
                />
                <span className="slider-hint">{s.hint}</span>
              </div>
            )
          })}

          <div className="slider-row">
            <div className="slider-head">
              <label htmlFor="sl-dur">Duration</label>
              <span className="slider-val">{durationH} h</span>
            </div>
            <input id="sl-dur" type="range" min={12} max={168} step={12}
              value={durationH} onChange={(e) => setDurationH(Number(e.target.value))} />
          </div>

          <div className="slider-row">
            <div className="slider-head">
              <label htmlFor="sl-sev">Scenario severity</label>
              <span className="slider-val">{severity.toFixed(1)} ×</span>
            </div>
            <input id="sl-sev" type="range" min={0.2} max={2.0} step={0.1}
              value={severity} onChange={(e) => setSeverity(Number(e.target.value))} />
            <span className="slider-hint">Scales the preset perturbation. 1.0 is nominal.</span>
          </div>

          <button className="btn danger mt-3" onClick={run} disabled={running}
            style={{ width: '100%', justifyContent: 'center' }}>
            <Play size={14} className={running ? 'spin' : ''} />
            {running ? 'Simulating…' : 'RUN SIMULATION'}
          </button>
        </Card>

        {/* ---------------------------------------------- before / after */}
        <Card title="3 · Before vs after" icon={<AlertTriangle size={14} />}
          tag={<DataTag kind="SIMULATED_SCENARIO" />}>
          {error ? <ErrorState error={error}
            hint="A simulation needs a modelled energy state and a real weather forecast." />
            : running ? <Loading label="Running the physics model…" rows={5} />
              : !result ? (
                <Empty icon={<ShieldAlert size={26} />}
                  message="No simulation run yet."
                  hint="Choose a scenario, adjust inputs, then press RUN SIMULATION." />
              ) : (
                <>
                  <div className="row-between wrap gap-3 mb-3">
                    <Stat label={`${result.label} · worst-case autonomy`} size="lg"
                      value={duration(result.survival_hours)}
                      tone={ratingTag(result.severity_rating) === 'emerg' ? 'emerg'
                        : ratingTag(result.severity_rating) === 'crit' ? 'crit'
                          : ratingTag(result.severity_rating) === 'warn' ? 'warn' : 'ok'} />
                    <div className="row gap-2 wrap">
                      <Tag kind={ratingTag(result.severity_rating)}>{result.severity_rating}</Tag>
                      {result.critical_load_secured
                        ? <Tag kind="ok">CRITICAL SECURED</Tag>
                        : <Tag kind="emerg">CRITICAL AT RISK</Tag>}
                    </div>
                  </div>

                  <div className="ba-grid mb-3">
                    <div className="ba-col before">
                      <div className="ba-label">Before · baseline</div>
                      <KV k="Risk" v={before?.risk_level} />
                      <KV k="Battery" v={`${num(before?.battery_soc_pct, 1)}%`} />
                      <KV k="Fuel" v={`${int(before?.fuel_level_l)} L`} />
                      <KV k="Load" v={`${num(before?.total_load_kw, 1)} kW`} />
                      <KV k="Critical" v={`${num(before?.critical_load_kw, 1)} kW`} />
                      <KV k="Renewable" v={`${num(before?.renewable_kw, 1)} kW`} />
                      <KV k="Autonomy" v={duration(before?.autonomy_hours)} />
                      <KV k="Shed" v={`${num(before?.shed_kw, 1)} kW`} />
                    </div>

                    <div className="ba-arrow"><ArrowRight size={18} /></div>

                    <div className="ba-col after">
                      <div className="ba-label">After · {result.label}</div>
                      <KV k="Risk" v={
                        <RiskBadge level={after?.risk_level}
                          color={`var(--pol-${riskTag(after?.risk_level) === 'ok' ? 'ok'
                            : riskTag(after?.risk_level) === 'warn' ? 'warn' : 'crit'})`} />
                      } />
                      <KV k="Battery" v={<>
                        {num(after?.battery_soc_pct, 1)}%{' '}
                        <Delta after={after?.battery_soc_pct} before={before?.battery_soc_pct} unit="%" />
                      </>} />
                      <KV k="Fuel" v={<>
                        {int(after?.fuel_level_l)} L{' '}
                        <Delta after={after?.fuel_level_l} before={before?.fuel_level_l} unit=" L" digits={0} />
                      </>} />
                      <KV k="Load" v={<>
                        {num(after?.total_load_kw, 1)} kW{' '}
                        <Delta after={after?.total_load_kw} before={before?.total_load_kw} unit=" kW" lowerIsBetter />
                      </>} />
                      <KV k="Critical" v={`${num(after?.critical_load_kw, 1)} kW`} />
                      <KV k="Renewable" v={<>
                        {num(after?.renewable_kw, 1)} kW{' '}
                        <Delta after={after?.renewable_kw} before={before?.renewable_kw} unit=" kW" />
                      </>} />
                      <KV k="Autonomy" v={<>
                        {duration(after?.autonomy_hours)}{' '}
                        <Delta after={after?.autonomy_hours} before={before?.autonomy_hours} unit=" h" digits={0} />
                      </>} />
                      <KV k="Shed" v={<>
                        {num(after?.shed_kw, 1)} kW{' '}
                        <Delta after={after?.shed_kw} before={before?.shed_kw} unit=" kW" lowerIsBetter />
                      </>} />
                    </div>
                  </div>

                  <div className="grid grid-3 mb-3">
                    <Stat label="Fuel used" size="sm" value={num(result.fuel_used_l, 0)} unit="L" />
                    <Stat label="Load shed" size="sm" value={num(result.load_shed_kwh, 0)} unit="kWh"
                      tone={result.load_shed_kwh > 0 ? 'warn' : undefined} />
                    <Stat label="Unserved critical" size="sm"
                      value={num(result.unserved_critical_kwh, 1)} unit="kWh"
                      tone={result.unserved_critical_kwh > 0 ? 'emerg' : 'ok'} />
                  </div>

                  <div className="small" style={{ lineHeight: 1.6 }}>{result.summary}</div>

                  {Object.keys(result.overrides_applied || {}).length > 0 && (
                    <div className="row gap-2 wrap mt-3">
                      <span className="tiny muted">Overrides applied:</span>
                      {Object.entries(result.overrides_applied).map(([k, v]) => (
                        <span className="driver-chip" key={k}>
                          {k.replace(/_/g, ' ')}: <span className="dv">{num(v, 1)}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </>
              )}
        </Card>
      </div>

      {/* ------------------------------------------------ recommendation */}
      {result?.recommendation && (
        <Card title="4 · Recommended action" icon={<ShieldAlert size={14} />}
          tag={<DataTag kind="SIMULATED_SCENARIO" />} className="mb-4">
          <div className={`rec-card u-${result.recommendation.urgency}`}>
            <div className="row gap-2 wrap mb-2">
              <Tag kind={result.recommendation.urgency === 'IMMEDIATE' ? 'emerg'
                : result.recommendation.urgency === 'URGENT' ? 'crit'
                  : result.recommendation.urgency === 'ELEVATED' ? 'warn' : 'ok'}>
                {result.recommendation.urgency}
              </Tag>
              <span className="tiny muted">for the simulated scenario</span>
            </div>
            <div className="rec-action" style={{ marginTop: 0 }}>
              <strong>Action ·</strong> {result.recommendation.action}
            </div>
            <WhyThisAction reasons={result.recommendation.reasons} />
            {result.recommendation.expected_benefit && (
              <div className="small muted mt-3">
                <strong>Expected benefit:</strong> {result.recommendation.expected_benefit}
              </div>
            )}
          </div>

          {result.actions_taken?.length > 0 && (
            <div className="mt-3">
              <div className="tiny upper muted mb-2">Automatic responses during the scenario</div>
              <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8, fontSize: 12 }}>
                {result.actions_taken.map((a, i) => <li key={i}>{a}</li>)}
              </ul>
            </div>
          )}
        </Card>
      )}

      {/* ------------------------------------------------ timeline */}
      {result && timeline.length > 0 && (
        <>
          <Card title="Scenario timeline" icon={<CloudSnow size={14} />}
            tag={<DataTag kind="SIMULATED_SCENARIO" />} className="mb-4">
            <CrisisTimelineChart data={timeline} height={320} />
            <Legend2 items={[
              { label: 'Load', color: '#0b4f3f' },
              { label: 'Renewable', color: '#2e9e7e' },
              { label: 'Diesel', color: '#a04a2f' },
              { label: 'Shed', color: '#b3261e' },
              { label: 'Battery SoC (right axis)', color: '#14a0b4' },
            ]} />
          </Card>

          <Card title="Hourly detail" icon={<CloudSnow size={14} />} bodyClass="flush">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th className="num">Hour</th>
                    <th>Time (UTC)</th>
                    <th className="num">Temp</th>
                    <th className="num">Wind</th>
                    <th className="num">Load</th>
                    <th className="num">Critical</th>
                    <th className="num">Wind kW</th>
                    <th className="num">Solar kW</th>
                    <th className="num">Diesel</th>
                    <th className="num">SoC</th>
                    <th className="num">Fuel</th>
                    <th className="num">Shed</th>
                  </tr>
                </thead>
                <tbody>
                  {result.timeline.map((t) => (
                    <tr key={t.hour} style={
                      t.unserved_kw > 0.01 ? { background: 'var(--pol-crit-bg)' }
                        : t.shed_kw > 0.1 ? { background: 'var(--pol-warn-bg)' } : undefined
                    }>
                      <td className="num tiny">{t.hour}</td>
                      <td className="mono tiny">{utc(t.timestamp)}</td>
                      <td className="num">{num(t.temperature_c, 1)}</td>
                      <td className="num">{num(t.wind_speed_ms, 1)}</td>
                      <td className="num strong">{num(t.load_kw, 1)}</td>
                      <td className="num">{num(t.critical_load_kw, 1)}</td>
                      <td className="num" style={{ color: 'var(--series-wind)' }}>{num(t.wind_kw, 1)}</td>
                      <td className="num" style={{ color: 'var(--series-solar)' }}>{num(t.solar_kw, 1)}</td>
                      <td className="num" style={{ color: 'var(--series-generator)' }}>{num(t.generator_kw, 1)}</td>
                      <td className="num">{num(t.battery_soc_pct, 1)}%</td>
                      <td className="num tiny">{int(t.fuel_level_l)}</td>
                      <td className="num">{t.shed_kw > 0.05 ? num(t.shed_kw, 1) : '—'}</td>
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
