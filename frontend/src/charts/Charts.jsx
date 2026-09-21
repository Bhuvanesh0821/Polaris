import {
  Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line,
  PolarAngleAxis, RadialBar, RadialBarChart, ReferenceArea, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { axisTime, num, utc } from '../utils/format'

/* Shared chart chrome: one place so every plot in POLARIS reads the same. */

const AXIS = {
  tick: { fontSize: 10.5, fill: 'var(--pol-muted)', fontFamily: 'IBM Plex Mono, monospace' },
  stroke: 'var(--pol-line)',
  tickLine: false,
  axisLine: { stroke: 'var(--pol-line)' },
}

const GRID = { stroke: 'var(--pol-line-soft)', strokeDasharray: '2 4', vertical: false }

export function ChartTooltip({ active, payload, label, unit = '', labelFormatter }) {
  if (!active || !payload?.length) return null
  return (
    <div className="tooltip-card">
      <div className="tt-time">{labelFormatter ? labelFormatter(label) : utc(label)}</div>
      {payload
        .filter((p) => p.value !== null && p.value !== undefined)
        .map((p, i) => (
          <div className="tt-row" key={i}>
            <span style={{ color: p.color || p.stroke, display: 'flex', alignItems: 'center', gap: 5 }}>
              <span className="legend-swatch" style={{ background: p.color || p.stroke }} />
              {p.name}
            </span>
            <span className="tt-val">
              {num(p.value, 1)}{p.unit || unit}
            </span>
          </div>
        ))}
    </div>
  )
}

export function Legend2({ items }) {
  return (
    <div className="chart-legend mt-2">
      {items.map((it) => (
        <span className="legend-item" key={it.label}>
          <span className="legend-swatch" style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  )
}

/* ------------------------------------------------------- generation mix --- */

export function GenerationMixChart({ data, height = 260, xKey = 'time', showLoad = true }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 6, right: 6, left: -18, bottom: 0 }}>
        <defs>
          <linearGradient id="gWind" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1264a3" stopOpacity={0.55} />
            <stop offset="100%" stopColor="#1264a3" stopOpacity={0.12} />
          </linearGradient>
          <linearGradient id="gSolar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#d99400" stopOpacity={0.6} />
            <stop offset="100%" stopColor="#d99400" stopOpacity={0.12} />
          </linearGradient>
          <linearGradient id="gGen" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#a04a2f" stopOpacity={0.5} />
            <stop offset="100%" stopColor="#a04a2f" stopOpacity={0.1} />
          </linearGradient>
        </defs>
        <CartesianGrid {...GRID} />
        <XAxis dataKey={xKey} {...AXIS} tickFormatter={axisTime} minTickGap={42} />
        <YAxis {...AXIS} width={46} label={{
          value: 'kW', angle: -90, position: 'insideLeft',
          style: { fontSize: 10, fill: 'var(--pol-faint)' }, offset: 24,
        }} />
        <Tooltip content={<ChartTooltip unit=" kW" />} />
        <Area type="monotone" dataKey="wind" name="Wind" stackId="gen"
          stroke="#1264a3" fill="url(#gWind)" strokeWidth={1.4} />
        <Area type="monotone" dataKey="solar" name="Solar" stackId="gen"
          stroke="#d99400" fill="url(#gSolar)" strokeWidth={1.4} />
        <Area type="monotone" dataKey="generator" name="Diesel" stackId="gen"
          stroke="#a04a2f" fill="url(#gGen)" strokeWidth={1.4} />
        {showLoad && (
          <Line type="monotone" dataKey="load" name="Station load" stroke="#0b4f3f"
            strokeWidth={2} dot={false} />
        )}
        {showLoad && (
          <Line type="monotone" dataKey="critical" name="Critical load" stroke="#b3261e"
            strokeWidth={1.4} strokeDasharray="4 3" dot={false} />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/* ------------------------------------------------------- forecast bands --- */

export function ForecastBandChart({
  data, height = 280, valueKey = 'value', p10Key = 'p10', p90Key = 'p90',
  color = '#0b4f3f', name = 'Forecast', unit = ' kW', bandName = '80% interval',
  extraLines = [],
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 6, right: 6, left: -18, bottom: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="time" {...AXIS} tickFormatter={axisTime} minTickGap={42} />
        <YAxis {...AXIS} width={46} />
        <Tooltip content={<ChartTooltip unit={unit} />} />
        <Area type="monotone" dataKey={p90Key} name={bandName} stroke="none"
          fill={color} fillOpacity={0.14} />
        <Area type="monotone" dataKey={p10Key} name=" " stroke="none"
          fill="#ffffff" fillOpacity={1} />
        <Line type="monotone" dataKey={valueKey} name={name} stroke={color}
          strokeWidth={2.2} dot={false} />
        {extraLines.map((l) => (
          <Line key={l.key} type="monotone" dataKey={l.key} name={l.name}
            stroke={l.color} strokeWidth={l.width || 1.5}
            strokeDasharray={l.dash || undefined} dot={false} />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/* ------------------------------------------------------------- soc / fuel --- */

export function SocChart({ data, height = 230, socFloor = 15, socReserve = 30 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 6, right: 6, left: -18, bottom: 0 }}>
        <defs>
          <linearGradient id="gSoc" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#14a0b4" stopOpacity={0.45} />
            <stop offset="100%" stopColor="#14a0b4" stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="time" {...AXIS} tickFormatter={axisTime} minTickGap={42} />
        <YAxis {...AXIS} width={46} domain={[0, 100]} />
        <Tooltip content={<ChartTooltip unit="%" />} />
        <ReferenceArea y1={0} y2={socFloor} fill="#b3261e" fillOpacity={0.07} />
        <ReferenceLine y={socFloor} stroke="#b3261e" strokeDasharray="3 3" strokeWidth={1}
          label={{ value: 'protected floor', position: 'insideBottomRight', fontSize: 9, fill: '#b3261e' }} />
        <ReferenceLine y={socReserve} stroke="#9a6200" strokeDasharray="3 3" strokeWidth={1}
          label={{ value: 'reserve', position: 'insideTopRight', fontSize: 9, fill: '#9a6200' }} />
        <Area type="monotone" dataKey="soc" name="State of charge" stroke="#14a0b4"
          strokeWidth={2} fill="url(#gSoc)" />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/* ------------------------------------------------------------- weather --- */

export function WeatherChart({ data, height = 250 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="time" {...AXIS} tickFormatter={axisTime} minTickGap={42} />
        <YAxis yAxisId="t" {...AXIS} width={46} />
        <YAxis yAxisId="w" orientation="right" {...AXIS} width={40} />
        <Tooltip content={<ChartTooltip />} />
        <ReferenceLine yAxisId="w" y={25} stroke="#b3261e" strokeDasharray="3 3"
          label={{ value: 'turbine cut-out', position: 'insideTopRight', fontSize: 9, fill: '#b3261e' }} />
        <Line yAxisId="t" type="monotone" dataKey="temperature" name="Temperature"
          stroke="#1264a3" strokeWidth={2} dot={false} unit=" °C" />
        <Line yAxisId="t" type="monotone" dataKey="windChill" name="Wind chill"
          stroke="#7fd4e2" strokeWidth={1.3} strokeDasharray="4 3" dot={false} unit=" °C" />
        <Line yAxisId="w" type="monotone" dataKey="wind" name="Wind speed"
          stroke="#0f6b54" strokeWidth={1.8} dot={false} unit=" m/s" />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/* ---------------------------------------------------------- simple bars --- */

export function PriorityBarChart({ data, height = 230 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 20, left: 4, bottom: 0 }}>
        <CartesianGrid stroke="var(--pol-line-soft)" strokeDasharray="2 4" horizontal={false} />
        <XAxis type="number" {...AXIS} />
        <YAxis type="category" dataKey="name" {...AXIS} width={128}
          tick={{ fontSize: 10.5, fill: 'var(--pol-text)' }} />
        <Tooltip content={<ChartTooltip unit=" kW" labelFormatter={(l) => l} />} />
        <Bar dataKey="value" name="Demand" radius={[0, 3, 3, 0]} barSize={15}>
          {data.map((d, i) => <Cell key={i} fill={d.color} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

export function ContributionChart({ data, height = 300 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 30, left: 4, bottom: 0 }}>
        <CartesianGrid stroke="var(--pol-line-soft)" strokeDasharray="2 4" horizontal={false} />
        <XAxis type="number" {...AXIS} />
        <YAxis type="category" dataKey="name" {...AXIS} width={165}
          tick={{ fontSize: 10, fill: 'var(--pol-text)' }} />
        <Tooltip content={<ChartTooltip unit=" kW" labelFormatter={(l) => l} />} />
        <ReferenceLine x={0} stroke="var(--pol-muted)" />
        <Bar dataKey="value" name="Contribution" radius={[0, 3, 3, 0]} barSize={13}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.value >= 0 ? '#b3261e' : '#1264a3'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

export function ImportanceChart({ data, height = 300 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 28, left: 4, bottom: 0 }}>
        <CartesianGrid stroke="var(--pol-line-soft)" strokeDasharray="2 4" horizontal={false} />
        <XAxis type="number" {...AXIS} unit="%" />
        <YAxis type="category" dataKey="name" {...AXIS} width={165}
          tick={{ fontSize: 10, fill: 'var(--pol-text)' }} />
        <Tooltip content={<ChartTooltip unit="%" labelFormatter={(l) => l} />} />
        <Bar dataKey="value" name="Importance" radius={[0, 3, 3, 0]} barSize={13} fill="#0f6b54" />
      </BarChart>
    </ResponsiveContainer>
  )
}

/* -------------------------------------------------------------- gauge --- */

export function Gauge({ value, max = 100, label, unit = '%', color = '#0f6b54', height = 150 }) {
  const pctv = Math.max(0, Math.min(100, (value / max) * 100))
  const data = [{ name: label, value: pctv, fill: color }]
  return (
    <div style={{ position: 'relative', height }}>
      <ResponsiveContainer width="100%" height="100%">
        <RadialBarChart innerRadius="68%" outerRadius="100%" data={data}
          startAngle={210} endAngle={-30} barSize={13}>
          <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
          <RadialBar background={{ fill: 'var(--pol-bg-alt)' }} dataKey="value"
            cornerRadius={7} isAnimationActive={false} />
        </RadialBarChart>
      </ResponsiveContainer>
      <div style={{
        position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', pointerEvents: 'none',
        paddingTop: 10,
      }}>
        <div className="num" style={{ fontSize: 24, fontWeight: 600, color: 'var(--pol-ink)' }}>
          {num(value, 1)}<span style={{ fontSize: 12, color: 'var(--pol-muted)' }}>{unit}</span>
        </div>
        <div className="tiny muted upper" style={{ marginTop: 1 }}>{label}</div>
      </div>
    </div>
  )
}

/* ---------------------------------------------------- crisis timeline --- */

export function CrisisTimelineChart({ data, height = 300 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
        <defs>
          <linearGradient id="gCrisisSoc" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#14a0b4" stopOpacity={0.4} />
            <stop offset="100%" stopColor="#14a0b4" stopOpacity={0.04} />
          </linearGradient>
        </defs>
        <CartesianGrid {...GRID} />
        <XAxis dataKey="hour" {...AXIS} label={{
          value: 'hours into scenario', position: 'insideBottom', offset: -2,
          style: { fontSize: 9.5, fill: 'var(--pol-faint)' },
        }} />
        <YAxis yAxisId="kw" {...AXIS} width={46} />
        <YAxis yAxisId="soc" orientation="right" {...AXIS} width={40} domain={[0, 100]} />
        <Tooltip content={<ChartTooltip labelFormatter={(h) => `Hour ${h}`} />} />
        <ReferenceLine yAxisId="soc" y={15} stroke="#b3261e" strokeDasharray="3 3" />
        <Area yAxisId="soc" type="monotone" dataKey="soc" name="Battery SoC"
          stroke="#14a0b4" strokeWidth={1.8} fill="url(#gCrisisSoc)" unit="%" />
        <Line yAxisId="kw" type="monotone" dataKey="load" name="Load"
          stroke="#0b4f3f" strokeWidth={2} dot={false} unit=" kW" />
        <Line yAxisId="kw" type="monotone" dataKey="renewable" name="Renewable"
          stroke="#2e9e7e" strokeWidth={1.6} dot={false} unit=" kW" />
        <Line yAxisId="kw" type="monotone" dataKey="generator" name="Diesel"
          stroke="#a04a2f" strokeWidth={1.6} dot={false} unit=" kW" />
        <Line yAxisId="kw" type="monotone" dataKey="shed" name="Shed"
          stroke="#b3261e" strokeWidth={1.4} strokeDasharray="3 3" dot={false} unit=" kW" />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
