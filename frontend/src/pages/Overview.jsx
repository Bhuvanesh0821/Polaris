import {
  Activity, AlertTriangle, BatteryCharging, Droplets, Fuel, Gauge, Lightbulb,
  Radio, ShieldAlert, Sun, Thermometer, Timer, Wind, Zap, CheckCircle2, Database,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { usePolaris } from '../hooks/usePolaris'
import {
  Card, DataTag, Empty, ErrorState, KV, Loading, LivePill, MeasuredStat, Note,
  RiskBadge, Stat, Tag, Bar, WhyThisAction,
} from '../components/Primitives'
import { GenerationMixChart } from '../charts/Charts'
import {
  ago, compassPoint, duration, int, num, pct, riskTag, severityTag, statusTag,
  urgencyTag, utc,
} from '../utils/format'

export default function Overview() {
  const {
    summary, loading, error, reload, liveStatus, ageSeconds, observedAt,
    weatherFields: W, banners, refreshResult,
  } = usePolaris()

  if (loading && !summary) {
    return <div className="page"><Card title="Connecting to POLARIS"><Loading rows={5} /></Card></div>
  }

  if (error && !summary) {
    return (
      <div className="page">
        <ErrorState
          error={error}
          onRetry={reload}
          hint="Make sure the backend is running and PostgreSQL is reachable. See README.md."
        />
      </div>
    )
  }

  const energy = summary?.energy
  const survival = summary?.survival
  const opt = summary?.optimization
  const rec = summary?.top_recommendation
  const alerts = summary?.active_alerts || []
  const preview = summary?.forecast_preview || []
  const station = summary?.station || {}
  const stale = liveStatus !== 'LIVE'

  const mixData = preview.map((p) => ({
    time: p.target_time,
    wind: p.wind_kw,
    solar: p.solar_kw,
    generator: 0,
    load: energy?.total_load_kw ?? null,
    critical: energy?.critical_load_kw ?? null,
  }))

  const socTone = energy
    ? energy.battery_soc_pct <= 20 ? 'crit'
      : energy.battery_soc_pct <= 35 ? 'warn' : undefined
    : undefined

  return (
    <div className="page">
      {/* ---------------------------------------------- data-honesty header */}
      <div className="grid grid-2 mb-4" style={{ gridTemplateColumns: 'minmax(0,1.35fr) minmax(0,1fr)' }}>
        <Note kind="info">
          <strong>Live weather is real.</strong>{' '}
          {banners?.live_weather_note
            || 'Retrieved from Maitri (WMO 89514), an Indian Antarctic Programme station, via the WMO Global Telecommunication System.'}
        </Note>
        <Note kind="model">
          <strong>Energy figures are modelled.</strong>{' '}
          No public real-time electrical telemetry exists for any Indian Antarctic station.
          Battery, fuel, generator and load values below are model estimates driven by the live weather.
        </Note>
      </div>

      {stale && (
        <div className="mb-4">
          <Note kind="warn">
            <strong>Live source unavailable — displaying last successful observation.</strong>{' '}
            Last good reading {observedAt ? `${utc(observedAt)} (${ago(ageSeconds)})` : 'unknown'}.
            POLARIS does not substitute generated values.
          </Note>
        </div>
      )}

      {refreshResult && refreshResult.ok === false && (
        <div className="mb-4">
          <Note kind="crit">
            <strong>Refresh failed.</strong> {refreshResult.message || 'No provider responded.'}
          </Note>
        </div>
      )}

      {/* ------------------------------------------------ live weather strip */}
      <Card
        className="accent-green mb-4"
        title="Live Antarctic Conditions"
        icon={<Radio size={14} />}
        tag={<DataTag kind="REAL_LIVE_WEATHER" />}
        actions={<LivePill status={liveStatus} ageSeconds={ageSeconds} />}
        footer={
          <div className="row-between wrap gap-3">
            <span>
              {station.station_name} · {station.operator} · WMO {station.wmo_index} ·{' '}
              {num(station.latitude, 3)}°, {num(station.longitude, 3)}° · {int(station.elevation_m)} m
            </span>
            <span>Observed {utc(observedAt)}</span>
          </div>
        }
      >
        <div className="grid grid-6">
          <MeasuredStat label="Temperature" field={W.temperature_c} unit="°C"
            icon={<Thermometer size={11} />} />
          <MeasuredStat label="Wind speed" field={W.wind_speed_ms} unit="m/s"
            icon={<Wind size={11} />} />
          <MeasuredStat label="Wind direction" field={W.wind_direction_deg} unit=""
            icon={<Wind size={11} />}
            transform={(v) => (v == null ? '—' : `${compassPoint(v)} ${Math.round(v)}°`)} />
          <MeasuredStat label="Solar radiation" field={W.solar_radiation_wm2} unit="W/m²"
            digits={0} icon={<Sun size={11} />} />
          <MeasuredStat label="Humidity" field={W.humidity_pct} unit="%" digits={0}
            icon={<Droplets size={11} />} />
          <MeasuredStat label="Wind chill" field={W.wind_chill_c} unit="°C"
            icon={<Thermometer size={11} />} />
        </div>
      </Card>

      {/* --------------------------------------------------- modelled energy */}
      {!energy ? (
        <Card title="Modelled Energy State" icon={<Gauge size={14} />}
          tag={<DataTag kind="MODELLED_ENERGY" />}>
          <Empty
            icon={<Database size={26} />}
            message="No modelled energy state yet."
            hint="Press Refresh to pull live weather and run the model."
          />
        </Card>
      ) : (
        <>
          <div className="grid grid-5 mb-4">
            <Card className="accent-cyan" bodyClass="stat-card">
              <Stat
                label="Battery state of charge" icon={<BatteryCharging size={11} />}
                value={num(energy.battery_soc_pct, 1)} unit="%" tone={socTone}
                sub={`${int(energy.battery_stored_kwh)} kWh stored · ${energy.battery_mode.toLowerCase()}`}
              />
              <div className="mt-2">
                <Bar value={energy.battery_soc_pct} color="var(--pol-cyan-500)" markers={[15, 30]} />
              </div>
            </Card>

            <Card className="accent-green" bodyClass="stat-card">
              <Stat
                label="Station load" icon={<Zap size={11} />}
                value={num(energy.total_load_kw, 1)} unit="kW"
                sub={`${num(energy.critical_load_kw, 1)} kW critical · ${num(energy.deferrable_load_kw, 1)} kW deferrable`}
              />
              <div className="mt-2">
                <Bar value={energy.critical_load_kw} max={energy.total_load_kw || 1}
                  color="var(--pol-green-700)" />
              </div>
            </Card>

            <Card className="accent-blue" bodyClass="stat-card">
              <Stat
                label="Renewable generation" icon={<Wind size={11} />}
                value={num(energy.renewable_kw, 1)} unit="kW"
                sub={`Wind ${num(energy.wind_generation_kw, 1)} · Solar ${num(energy.solar_generation_kw, 1)} kW`}
              />
              <div className="mt-2">
                <Bar value={energy.renewable_fraction * 100} color="var(--pol-blue-600)" />
                <div className="tiny muted mt-2">{pct(energy.renewable_fraction * 100, 1)} of supply</div>
              </div>
            </Card>

            <Card bodyClass="stat-card">
              <Stat
                label="Fuel remaining" icon={<Fuel size={11} />}
                value={int(energy.fuel_level_l)} unit="L"
                tone={energy.fuel_level_pct < 20 ? 'crit' : energy.fuel_level_pct < 35 ? 'warn' : undefined}
                sub={`${pct(energy.fuel_level_pct, 1)} of tank · ${num(energy.fuel_consumed_l, 1)} L/h burn`}
              />
              <div className="mt-2">
                <Bar value={energy.fuel_level_pct} color="var(--series-fuel)" markers={[15]} />
              </div>
            </Card>

            <Card bodyClass="stat-card">
              <Stat
                label="Diesel generator" icon={<Activity size={11} />}
                value={num(energy.generator_output_kw, 1)} unit="kW"
                sub={
                  energy.generators_running > 0
                    ? `${energy.generators_running} unit online · ${num(energy.generator_loading_pct, 0)}% loading`
                    : 'All units offline'
                }
              />
              <div className="mt-2">
                {energy.generators_running > 0 ? (
                  <Bar value={energy.generator_loading_pct} color="var(--series-generator)" markers={[30]} />
                ) : (
                  <div className="tiny muted">Renewables + storage carrying the station</div>
                )}
              </div>
            </Card>
          </div>

          {/* ------------------------------------------- survival + forecast */}
          <div className="grid grid-3-2 mb-4">
            <Card
              title="Renewable Generation Forecast · next 24 h"
              icon={<Sun size={14} />}
              tag={<DataTag kind="AI_FORECAST" />}
              actions={<Link to="/renewable-forecast" className="btn sm">Open forecast</Link>}
            >
              {mixData.length ? (
                <GenerationMixChart data={mixData} height={250} showLoad />
              ) : (
                <Empty message="No forecast generated yet." hint="Run a refresh to produce one." />
              )}
            </Card>

            <div className="grid" style={{ gap: 12, alignContent: 'start' }}>
              <Card
                title="Operational Risk"
                icon={<ShieldAlert size={14} />}
                tag={<DataTag kind="MODELLED_ENERGY" />}
              >
                {survival?.risk ? (
                  <>
                    <div className="row-between mb-3">
                      <Stat
                        label="Risk level" size="lg" value={survival.risk.level}
                        tone={riskTag(survival.risk.level) === 'emerg' ? 'emerg'
                          : riskTag(survival.risk.level) === 'crit' ? 'crit'
                            : riskTag(survival.risk.level) === 'warn' ? 'warn' : 'ok'}
                        sub={`composite score ${num(survival.risk.score_pct, 0)} / 100`}
                      />
                      <RiskBadge level={survival.risk.level}
                        scorePct={survival.risk.score_pct}
                        color={survival.risk.color} />
                    </div>
                    <div className="risk-meter mb-3">
                      <div className="risk-meter-fill"
                        style={{ width: `${survival.risk.score_pct}%`,
                          background: survival.risk.color }} />
                    </div>
                    <div className="small" style={{ lineHeight: 1.6 }}>
                      {survival.risk.headline}
                    </div>
                    {survival.risk.top_drivers?.length > 0 && (
                      <div className="row gap-2 wrap mt-3">
                        <span className="tiny muted">Top drivers:</span>
                        {survival.risk.top_drivers.map((d) => (
                          <span className="driver-chip" key={d}>{d}</span>
                        ))}
                      </div>
                    )}
                  </>
                ) : <Empty message="Awaiting first model run." />}
              </Card>

              <Card
                title="Energy Survival Estimate"
                icon={<Timer size={14} />}
                tag={<DataTag kind="MODELLED_ENERGY" />}
                actions={<Link to="/survival" className="btn sm">Details</Link>}
              >
                {survival ? (
                  <>
                    <div className="row-between mb-3">
                      <Stat
                        label="Estimated autonomy" size="lg"
                        value={survival.estimated_autonomy_hours == null
                          ? 'Indefinite'
                          : duration(survival.estimated_autonomy_hours)}
                        tone={statusTag(survival.status) === 'crit' ? 'crit'
                          : statusTag(survival.status) === 'warn' ? 'warn' : 'ok'}
                        sub={`Limited by ${survival.limited_by}`}
                      />
                      <Tag kind={statusTag(survival.status)}>{survival.status}</Tag>
                    </div>
                    {survival.modes && (
                      <div className="grid grid-3 mb-3" style={{ gap: 8 }}>
                        {[
                          ['Normal', survival.modes.normal_hours],
                          ['Critical only', survival.modes.critical_only_hours],
                          ['Crisis', survival.modes.crisis_hours],
                        ].map(([label, hrs]) => (
                          <div key={label} style={{
                            padding: '7px 9px', background: 'var(--pol-bg)',
                            borderRadius: 'var(--r-sm)', border: '1px solid var(--pol-line-soft)',
                          }}>
                            <div className="tiny muted upper">{label}</div>
                            <div className="num strong" style={{ fontSize: 13 }}>
                              {hrs == null ? '∞' : duration(hrs)}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    <KV k="Battery endurance" v={duration(survival.battery_hours)} />
                    <KV k="Fuel endurance" v={duration(survival.fuel_hours)} />
                    <KV k="Critical demand" v={`${num(survival.critical_demand_kw, 1)} kW`} />
                    <KV k="Usable fuel" v={`${int(survival.fuel_usable_l)} L`} />
                  </>
                ) : (
                  <Empty message="Awaiting first model run." />
                )}
              </Card>
            </div>
          </div>
        </>
      )}

      {/* ------------------------------------------ recommendation + alerts */}
      <div className="grid grid-2-1">
        <Card
          title="Top AI Recommendation"
          icon={<Lightbulb size={14} />}
          tag={<DataTag kind="AI_FORECAST" />}
          actions={<Link to="/explainability" className="btn sm">Why?</Link>}
        >
          {rec ? (
            <div className={`rec-card u-${rec.urgency}`} style={{ border: 'none', padding: 0, background: 'transparent' }}>
              <div className="row gap-2 wrap mb-2">
                <Tag kind={urgencyTag(rec.urgency)}>{rec.urgency}</Tag>
                <Tag kind="neutral">{rec.category.replace(/_/g, ' ')}</Tag>
                {rec.confidence != null && (
                  <span className="tiny muted">confidence {pct(rec.confidence * 100, 0)}</span>
                )}
              </div>
              <div className="rec-title">{rec.title}</div>
              <div className="rec-action"><strong>Action ·</strong> {rec.action}</div>
              <div className="rec-rationale">{rec.rationale}</div>
              <WhyThisAction reasons={rec.reasons} />
              {rec.drivers?.length > 0 && (
                <div className="row gap-2 wrap mt-3">
                  {rec.drivers.map((d, i) => (
                    <span className="driver-chip" key={i}>
                      {d.factor}: <span className="dv">{d.value}</span>
                    </span>
                  ))}
                </div>
              )}
              {rec.expected_benefit && (
                <div className="small muted mt-3">
                  <strong>Expected benefit:</strong> {rec.expected_benefit}
                </div>
              )}
            </div>
          ) : (
            <Empty message="No recommendation yet." hint="Generated after the first model run." />
          )}
        </Card>

        <Card
          title="Active Alerts"
          icon={<AlertTriangle size={14} />}
          bodyClass="flush"
          actions={<Link to="/alerts" className="btn sm">All alerts</Link>}
        >
          {alerts.length === 0 ? (
            <div className="empty">
              <CheckCircle2 size={26} style={{ color: 'var(--pol-ok)', opacity: 0.8 }} />
              <div>No active alerts</div>
              <div className="tiny mt-2">Every monitored parameter is inside its operating band.</div>
            </div>
          ) : (
            <div>
              {alerts.slice(0, 6).map((a) => (
                <div key={a.id} className={`alert-row sev-${a.severity}`}>
                  <AlertTriangle size={14} style={{ flex: '0 0 14px', marginTop: 2 }} />
                  <div className="grow">
                    <div className="row-between gap-2">
                      <span className="alert-title">{a.title}</span>
                      <Tag kind={severityTag(a.severity)}>{a.severity}</Tag>
                    </div>
                    <div className="alert-msg">{a.message}</div>
                    {a.recommended_action && (
                      <div className="tiny muted mt-2">→ {a.recommended_action}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* ------------------------------------------------------ optimisation */}
      {opt && (
        <div className="mt-4">
          <Card
            title="Latest Optimisation Result"
            icon={<Gauge size={14} />}
            tag={<DataTag kind="MODELLED_ENERGY" />}
            actions={<Link to="/optimization" className="btn sm">Open optimiser</Link>}
          >
            <div className="grid grid-5">
              <Stat label="Fuel saved" value={num(opt.fuel_saved_l, 1)} unit="L"
                sub={`${pct(opt.fuel_saved_pct, 1)} vs baseline`} size="sm"
                tone={opt.fuel_saved_l > 0 ? 'ok' : undefined} />
              <Stat label="Baseline burn" value={num(opt.baseline_fuel_l, 0)} unit="L" size="sm"
                sub={`over ${opt.horizon_h} h`} />
              <Stat label="Optimised burn" value={num(opt.optimized_fuel_l, 0)} unit="L" size="sm"
                sub="merit-order dispatch" />
              <Stat label="Renewable share" value={pct(opt.renewable_fraction * 100, 1)} size="sm"
                sub="of served energy" />
              <Stat label="CO₂ avoided" value={num(opt.co2_avoided_kg, 0)} unit="kg" size="sm"
                sub={`${num(opt.load_deferred_kwh, 0)} kWh deferred`} />
            </div>
            {opt.rationale?.length > 0 && (
              <ul className="small muted mt-3" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
                {opt.rationale.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
