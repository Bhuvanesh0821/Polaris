/** Formatting helpers. Every number the operator reads goes through here. */

export function num(v, digits = 1, fallback = '—') {
  if (v === null || v === undefined || Number.isNaN(v)) return fallback
  return Number(v).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function int(v, fallback = '—') {
  if (v === null || v === undefined || Number.isNaN(v)) return fallback
  return Math.round(Number(v)).toLocaleString()
}

export function pct(v, digits = 1, fallback = '—') {
  if (v === null || v === undefined || Number.isNaN(v)) return fallback
  return `${num(v, digits)}%`
}

/** Hours -> "3 d 4 h" / "18 h" / "42 min" */
export function duration(hours, fallback = '—') {
  if (hours === null || hours === undefined || Number.isNaN(hours)) return fallback
  if (!Number.isFinite(hours)) return 'Indefinite'
  if (hours < 1) return `${Math.round(hours * 60)} min`
  if (hours < 48) return `${num(hours, 1)} h`
  const d = Math.floor(hours / 24)
  const h = Math.round(hours % 24)
  return h ? `${d} d ${h} h` : `${d} d`
}

/** Seconds since an event -> "just now" / "4 min ago" / "2 h 10 m ago" */
export function ago(seconds, fallback = '—') {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return fallback
  const s = Math.max(0, Math.round(seconds))
  if (s < 45) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  const h = Math.floor(s / 3600)
  const m = Math.round((s % 3600) / 60)
  if (h < 24) return m ? `${h} h ${m} m ago` : `${h} h ago`
  const d = Math.floor(h / 24)
  return `${d} d ${h % 24} h ago`
}

export function utc(iso, withSeconds = false) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const p = (n) => String(n).padStart(2, '0')
  const base = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`
  return withSeconds ? `${base}:${p(d.getUTCSeconds())} UTC` : `${base} UTC`
}

export function hhmm(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`
}

/** Compact axis label: "21 Sep 06" */
export function axisTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getUTCDate()} ${months[d.getUTCMonth()]} ${p(d.getUTCHours())}`
}

export function compassPoint(deg) {
  if (deg === null || deg === undefined || Number.isNaN(deg)) return '—'
  const pts = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
    'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
  return pts[Math.round(((deg % 360) / 22.5)) % 16]
}

export function severityTag(sev) {
  return { INFO: 'info', WARNING: 'warn', CRITICAL: 'crit', EMERGENCY: 'emerg' }[sev] || 'neutral'
}

export function urgencyTag(u) {
  return { IMMEDIATE: 'emerg', URGENT: 'crit', ELEVATED: 'warn', ROUTINE: 'ok' }[u] || 'neutral'
}

export function statusTag(s) {
  return { SECURE: 'ok', ADEQUATE: 'ok', WARNING: 'warn', CRITICAL: 'crit' }[s] || 'neutral'
}

export function riskTag(level) {
  return {
    LOW: 'ok', MODERATE: 'ok', ELEVATED: 'warn', HIGH: 'crit', SEVERE: 'emerg',
  }[level] || 'neutral'
}

export function ratingTag(r) {
  return {
    MANAGEABLE: 'ok', ELEVATED: 'warn', SEVERE: 'crit', CATASTROPHIC: 'emerg',
  }[r] || 'neutral'
}

export const PRIORITY_LABEL = {
  P1_LIFE_CRITICAL: 'P1 · Life Critical',
  P2_SCIENCE_CRITICAL: 'P2 · Science Critical',
  P3_OPERATIONAL: 'P3 · Operational',
  P4_DEFERRABLE: 'P4 · Deferrable',
}

export const SERIES = {
  wind: '#1264a3',
  solar: '#d99400',
  generator: '#a04a2f',
  load: '#0b4f3f',
  critical: '#b3261e',
  battery: '#14a0b4',
  fuel: '#6b5b95',
  renewable: '#2e9e7e',
}
