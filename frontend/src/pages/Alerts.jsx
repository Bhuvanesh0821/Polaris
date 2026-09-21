import { useState } from 'react'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { usePolaris, useEndpoint } from '../hooks/usePolaris'
import api from '../services/api'
import { Card, DataTag, ErrorState, Loading, Tag } from '../components/Primitives'
import { num, severityTag, utc } from '../utils/format'

const FILTERS = [
  { label: 'Active', value: 'ACTIVE' },
  { label: 'Resolved', value: 'RESOLVED' },
  { label: 'All', value: '' },
]

export default function Alerts() {
  const { tick } = usePolaris()
  const [filter, setFilter] = useState('ACTIVE')
  const { data, loading, error, reload } = useEndpoint(
    () => api.alerts(filter || undefined), [filter, tick],
  )

  const rows = data || []
  const bySeverity = rows.reduce((acc, a) => {
    acc[a.severity] = (acc[a.severity] || 0) + 1
    return acc
  }, {})

  return (
    <div className="page">
      <div className="page-intro">
        <h2>Alerts</h2>
        <p>
          Threshold and anomaly conditions raised against the modelled energy state. Alerts
          auto-resolve when the condition clears on the next model run.
        </p>
      </div>

      <div className="grid grid-4 mb-4">
        {['EMERGENCY', 'CRITICAL', 'WARNING', 'INFO'].map((sev) => (
          <Card key={sev} bodyClass="stat-card">
            <div className="stat">
              <span className="stat-label">{sev}</span>
              <span className="stat-value" style={{
                color: bySeverity[sev]
                  ? `var(--pol-${severityTag(sev) === 'emerg' ? 'emerg' : severityTag(sev) === 'crit' ? 'crit' : severityTag(sev) === 'warn' ? 'warn' : 'info'})`
                  : 'var(--pol-faint)',
              }}>
                {bySeverity[sev] || 0}
              </span>
            </div>
          </Card>
        ))}
      </div>

      <Card
        title="Alert log"
        icon={<AlertTriangle size={14} />}
        tag={<DataTag kind="MODELLED_ENERGY" />}
        bodyClass="flush"
        actions={
          <div className="seg-control">
            {FILTERS.map((f) => (
              <button key={f.value} className={filter === f.value ? 'active' : ''}
                onClick={() => setFilter(f.value)}>{f.label}</button>
            ))}
          </div>
        }
      >
        {loading ? <div style={{ padding: 16 }}><Loading rows={4} /></div>
          : error ? <div style={{ padding: 16 }}><ErrorState error={error} onRetry={reload} /></div>
            : rows.length === 0 ? (
              <div className="empty">
                <CheckCircle2 size={26} style={{ color: 'var(--pol-ok)', opacity: 0.8 }} />
                <div>No {filter ? filter.toLowerCase() : ''} alerts</div>
                <div className="tiny mt-2">
                  Every monitored parameter is inside its operating band.
                </div>
              </div>
            ) : (
              <div>
                {rows.map((a) => (
                  <div key={a.id} className={`alert-row sev-${a.severity}`}>
                    <AlertTriangle size={15} style={{ flex: '0 0 15px', marginTop: 2 }} />
                    <div className="grow">
                      <div className="row-between gap-2 wrap">
                        <span className="alert-title">{a.title}</span>
                        <span className="row gap-2">
                          <Tag kind={severityTag(a.severity)}>{a.severity}</Tag>
                          <Tag kind={a.status === 'ACTIVE' ? 'warn' : 'ok'}>{a.status}</Tag>
                        </span>
                      </div>
                      <div className="alert-msg">{a.message}</div>
                      <div className="row gap-3 wrap tiny muted mt-2">
                        <span className="mono">{a.code}</span>
                        {a.subsystem && <span>subsystem: {a.subsystem}</span>}
                        {a.metric_name && (
                          <span className="mono">
                            {a.metric_name} = {num(a.metric_value, 2)}
                            {a.threshold_value != null && ` (threshold ${num(a.threshold_value, 2)})`}
                          </span>
                        )}
                        <span>raised {utc(a.raised_at)}</span>
                        {a.resolved_at && <span>resolved {utc(a.resolved_at)}</span>}
                      </div>
                      {a.recommended_action && (
                        <div className="small mt-2" style={{ fontWeight: 500 }}>
                          → {a.recommended_action}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
      </Card>
    </div>
  )
}
