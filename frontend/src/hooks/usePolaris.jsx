import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import api from '../services/api'

const PolarisContext = createContext(null)

const DEFAULT_POLL_MS = 60000

/**
 * Holds the shared live state: the dashboard summary, refresh status and any
 * connection error. Everything that needs "is the data live?" reads it here
 * so the answer is consistent across every page.
 */
export function PolarisProvider({ children }) {
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)
  const [lastFetchedAt, setLastFetchedAt] = useState(null)
  const [autoPoll, setAutoPoll] = useState(true)
  const [pollMs, setPollMs] = useState(DEFAULT_POLL_MS)
  const [refreshResult, setRefreshResult] = useState(null)
  const [tick, setTick] = useState(0)

  // StrictMode mounts, unmounts and remounts in development. The flag must be
  // re-armed on every mount, otherwise the simulated unmount latches it false
  // and every subsequent setState is skipped - leaving the UI on its skeleton.
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true)
    try {
      const data = await api.dashboard()
      if (!mounted.current) return
      setSummary(data)
      setError(null)
      setLastFetchedAt(new Date().toISOString())
    } catch (err) {
      if (!mounted.current) return
      setError(err.message || String(err))
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [])

  /** Manual Refresh button: forces a live provider pull + AI rerun. */
  const refreshNow = useCallback(async () => {
    setRefreshing(true)
    setRefreshResult(null)
    try {
      const res = await api.refresh(true)
      if (!mounted.current) return res
      setRefreshResult(res)
      await load({ silent: true })
      setTick((t) => t + 1)
      return res
    } catch (err) {
      if (mounted.current) {
        setRefreshResult({ ok: false, message: err.message })
        setError(err.message)
      }
      return { ok: false, message: err.message }
    } finally {
      if (mounted.current) setRefreshing(false)
    }
  }, [load])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!autoPoll) return undefined
    const id = setInterval(() => load({ silent: true }), pollMs)
    return () => clearInterval(id)
  }, [autoPoll, pollMs, load])

  // Re-check as soon as the operator returns to the tab.
  useEffect(() => {
    const onVis = () => { if (!document.hidden) load({ silent: true }) }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [load])

  const value = useMemo(() => {
    const lw = summary?.live_weather
    return {
      summary,
      loading,
      refreshing,
      error,
      lastFetchedAt,
      refreshResult,
      tick,
      autoPoll,
      setAutoPoll,
      pollMs,
      setPollMs,
      reload: load,
      refreshNow,
      // convenience selectors
      liveStatus: lw?.status || (error ? 'FAILED' : 'LOADING'),
      isLive: Boolean(lw?.is_live),
      isStale: Boolean(lw?.is_stale),
      observedAt: lw?.observed_at || null,
      ageSeconds: lw?.age_seconds ?? null,
      primarySource: lw?.primary_source || null,
      weatherFields: lw?.fields || {},
      banners: summary?.banners || {},
      station: summary?.station || {},
      alerts: summary?.active_alerts || [],
      scheduler: summary?.scheduler || {},
    }
  }, [summary, loading, refreshing, error, lastFetchedAt, refreshResult, tick,
    autoPoll, pollMs, load, refreshNow])

  return <PolarisContext.Provider value={value}>{children}</PolarisContext.Provider>
}

export function usePolaris() {
  const ctx = useContext(PolarisContext)
  if (!ctx) throw new Error('usePolaris must be used inside <PolarisProvider>')
  return ctx
}

/**
 * Generic fetch hook with loading/error state.
 * `deps` re-runs the fetch; `refreshKey` from usePolaris ties a page to the
 * global refresh so a manual Refresh updates every panel.
 */
export function useEndpoint(fetcher, deps = [], { immediate = true } = {}) {
  const [data, setData] = useState(null)
  const [meta, setMeta] = useState(null)
  const [loading, setLoading] = useState(immediate)
  const [error, setError] = useState(null)
  const mounted = useRef(true)

  // See the note in PolarisProvider: re-arm on mount for StrictMode.
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const run = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetcher()
      if (!mounted.current) return
      if (res && typeof res === 'object' && 'data' in res && 'provenance' in res) {
        setData(res.data)
        setMeta({ provenance: res.provenance, notice: res.notice, generatedAt: res.generatedAt })
      } else {
        setData(res)
        setMeta(null)
      }
      setError(null)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message || String(err))
      setData(null)
    } finally {
      if (mounted.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => { if (immediate) run() }, [run, immediate])

  return { data, meta, loading, error, reload: run, setData }
}
