import axios from 'axios'

const BASE = import.meta.env.VITE_API_URL || '/api'

export const client = axios.create({
  baseURL: BASE,
  timeout: 90000,
  headers: { 'Content-Type': 'application/json' },
})

/** Normalise every failure into a message a human can act on. */
function toError(err) {
  if (err.code === 'ECONNABORTED') {
    return new Error('Request timed out. The backend may still be starting up.')
  }
  if (!err.response) {
    return new Error(
      'Cannot reach the POLARIS backend. Start it with: uvicorn app.main:app --port 8000',
    )
  }
  const { status, data } = err.response
  const detail =
    data?.detail || data?.error || data?.message || err.message || 'Unknown error'
  const e = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  e.status = status
  e.payload = data
  return e
}

client.interceptors.response.use(
  (r) => r,
  (err) => Promise.reject(toError(err)),
)

/** Unwrap the {data, provenance, ...} envelope while keeping the metadata. */
function unwrap(res) {
  const body = res.data
  if (body && typeof body === 'object' && 'data' in body && 'provenance' in body) {
    return { data: body.data, provenance: body.provenance, notice: body.notice, generatedAt: body.generated_at }
  }
  return { data: body, provenance: null, notice: null, generatedAt: null }
}

export const api = {
  // --- system ---
  health: () => client.get('/health').then((r) => r.data),
  dashboard: () => client.get('/dashboard/summary').then((r) => r.data),
  modelInfo: () => client.get('/model/info').then((r) => r.data),
  retrain: () => client.post('/model/retrain').then((r) => r.data),
  schedulerStatus: () => client.get('/scheduler/status').then((r) => r.data),

  // --- weather (REAL LIVE) ---
  currentWeather: () => client.get('/weather/current').then(unwrap),
  weatherHistory: (hours = 48, includeForecast = false) =>
    client
      .get('/weather', { params: { hours, include_forecast: includeForecast } })
      .then(unwrap),
  refresh: (runAi = true) =>
    client.post('/weather/refresh', null, { params: { run_ai: runAi } }).then((r) => r.data),
  sources: () => client.get('/weather/sources').then((r) => r.data),
  providers: () => client.get('/weather/providers').then((r) => r.data),
  stations: () => client.get('/weather/stations').then((r) => r.data),

  // --- energy (MODELLED) ---
  energyStatus: () => client.get('/energy/status').then(unwrap),
  energyHistory: (hours = 72) =>
    client.get('/energy/history', { params: { hours } }).then(unwrap),
  loadProfile: () => client.get('/energy/load-profile').then(unwrap),

  // --- forecasts (AI) ---
  loadForecast: (hours = 48) =>
    client.get('/load/forecast', { params: { hours } }).then(unwrap),
  renewableForecast: (hours = 48) =>
    client.get('/renewable/forecast', { params: { hours } }).then(unwrap),

  // --- optimization ---
  runOptimization: (body) => client.post('/optimization/run', body).then(unwrap),
  latestOptimization: () => client.get('/optimization/latest').then(unwrap),

  // --- survival ---
  survival: (generatorsAvailable) =>
    client
      .get('/survival-analysis', {
        params:
          generatorsAvailable === undefined || generatorsAvailable === null
            ? {}
            : { generators_available: generatorsAvailable },
      })
      .then(unwrap),

  // --- crisis (SIMULATED) ---
  scenarios: () => client.get('/crisis/scenarios').then((r) => r.data),
  simulate: (body) => client.post('/crisis/simulate', body).then(unwrap),
  crisisHistory: (limit = 20) =>
    client.get('/crisis/history', { params: { limit } }).then((r) => r.data),

  // --- decision layer ---
  recommendations: (activeOnly = true) =>
    client.get('/recommendations', { params: { active_only: activeOnly } }).then(unwrap),
  alerts: (status) =>
    client.get('/alerts', { params: status ? { status } : {} }).then(unwrap),
  explainability: () => client.get('/explainability').then(unwrap),
}

export default api
