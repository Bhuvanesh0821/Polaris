import { useMemo } from 'react'
import { BrainCircuit, GitBranch, Lightbulb, Target } from 'lucide-react'
import { usePolaris, useEndpoint } from '../hooks/usePolaris'
import api from '../services/api'
import {
  Card, DataTag, Empty, ErrorState, KV, Loading, Stat, Tag, WhyThisAction,
} from '../components/Primitives'
import { ContributionChart, ImportanceChart } from '../charts/Charts'
import { num, pct, urgencyTag, utc } from '../utils/format'

export default function Explainability() {
  const { tick } = usePolaris()
  const xai = useEndpoint(() => api.explainability(), [tick])
  const recs = useEndpoint(() => api.recommendations(true), [tick])

  const d = xai.data

  const contributions = useMemo(() => (d?.contributions || [])
    .slice(0, 10)
    .map((c) => ({
      name: c.description.length > 44 ? `${c.description.slice(0, 42)}…` : c.description,
      value: c.contribution_kw,
    }))
    .reverse(), [d])

  const importance = useMemo(() => (d?.global_importance || [])
    .slice(0, 10)
    .map((g) => ({
      name: g.description.length > 44 ? `${g.description.slice(0, 42)}…` : g.description,
      value: g.importance_pct,
    }))
    .reverse(), [d])

  return (
    <div className="page">
      <div className="page-intro">
        <h2>Explainable AI</h2>
        <p>
          Operators at an isolated station will not act on an unexplained number. Every POLARIS
          output can answer <em>why</em> — at the model level, the prediction level and the
          dispatch-decision level.
        </p>
      </div>

      {xai.error ? (
        <ErrorState error={xai.error} onRetry={xai.reload}
          hint="Explanations become available once the models have trained on real weather history." />
      ) : xai.loading ? (
        <Card><Loading rows={5} /></Card>
      ) : !d ? (
        <Card><Empty message="No explanation available yet." /></Card>
      ) : (
        <>
          <div className="grid grid-2-1 mb-4">
            <Card title="Prediction narrative" icon={<Lightbulb size={14} />}
              tag={<DataTag kind="AI_FORECAST" />}
              footer={`Method: ${d.method}`}>
              <div className="row gap-4 wrap mb-3">
                <Stat label="Prediction" value={num(d.prediction, 1)} unit="kW" size="sm" />
                <Stat label="Typical for this plant" value={num(d.baseline, 1)} unit="kW" size="sm" />
                <Stat label="Difference" size="sm"
                  value={`${d.prediction - d.baseline >= 0 ? '+' : ''}${num(d.prediction - d.baseline, 1)}`}
                  unit="kW"
                  tone={d.prediction - d.baseline > 0 ? 'warn' : 'ok'} />
                {d.timestamp && (
                  <Stat label="For hour" value={utc(d.timestamp)} size="sm" />
                )}
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.7 }}>{d.narrative}</div>

              {d.counterfactuals?.length > 0 && (
                <div className="mt-4">
                  <div className="tiny upper muted mb-2">Counterfactuals</div>
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, lineHeight: 1.7 }}>
                    {d.counterfactuals.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
              )}
            </Card>

            <Card title="Dispatch decision" icon={<GitBranch size={14} />}
              tag={<DataTag kind="MODELLED_ENERGY" />}>
              {d.dispatch_explanation ? (
                <>
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.75 }}>
                    {d.dispatch_explanation.reasons.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                  <div className="mt-3">
                    <div className="tiny upper muted mb-2">Merit order</div>
                    <div className="row gap-2 wrap">
                      {d.dispatch_explanation.merit_order.map((m, i) => (
                        <span className="driver-chip" key={m}>
                          <span className="dv">{i + 1}</span> {m}
                        </span>
                      ))}
                    </div>
                  </div>
                </>
              ) : <Empty message="No dispatch decision to explain yet." />}
            </Card>
          </div>

          <div className="grid grid-2 mb-4">
            <Card title="Local attribution · this prediction" icon={<Target size={14} />}
              tag={<DataTag kind="AI_FORECAST" />}
              footer="Each bar: how much the prediction would move if that feature were at its typical value.">
              {contributions.length
                ? <ContributionChart data={contributions} height={330} />
                : <Empty message="No contributions computed." />}
            </Card>

            <Card title="Global feature importance" icon={<BrainCircuit size={14} />}
              tag={<DataTag kind="AI_FORECAST" />}
              footer="Permutation importance over the training set — what the model relies on overall.">
              {importance.length
                ? <ImportanceChart data={importance} height={330} />
                : <Empty message="No importance computed." />}
            </Card>
          </div>

          <Card title="Feature contribution detail" icon={<Target size={14} />}
            bodyClass="flush" className="mb-4">
            <div className="table-wrap" style={{ maxHeight: 340 }}>
              <table className="table">
                <thead>
                  <tr>
                    <th className="num">#</th>
                    <th>Feature</th>
                    <th>Meaning</th>
                    <th className="num">Value</th>
                    <th className="num">Contribution</th>
                    <th>Direction</th>
                  </tr>
                </thead>
                <tbody>
                  {(d.contributions || []).map((c) => (
                    <tr key={c.feature}>
                      <td className="num tiny">{c.rank}</td>
                      <td className="mono tiny">{c.feature}</td>
                      <td className="tiny muted">{c.description}</td>
                      <td className="num">{num(c.value, 2)}</td>
                      <td className="num strong" style={{
                        color: c.contribution_kw > 0 ? 'var(--pol-crit)' : 'var(--pol-blue-600)',
                      }}>
                        {c.contribution_kw >= 0 ? '+' : ''}{num(c.contribution_kw, 2)} kW
                      </td>
                      <td>
                        <Tag kind={c.direction === 'increases' ? 'crit'
                          : c.direction === 'decreases' ? 'info' : 'neutral'}>
                          {c.direction}
                        </Tag>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="grid grid-2">
            <Card title="Model cards" icon={<BrainCircuit size={14} />}>
              {Object.entries(d.model_reports || {}).map(([name, rep]) => (
                rep && Object.keys(rep).length > 0 && (
                  <div key={name} style={{ padding: '10px 0', borderBottom: '1px solid var(--pol-line-soft)' }}>
                    <div className="strong small mb-2">{name.replace(/_/g, ' ')}</div>
                    <KV k="Type" v={rep.model_type || '—'} />
                    <KV k="Version" v={rep.model_version || '—'} />
                    <KV k="Training samples" v={rep.n_samples ?? '—'} />
                    {rep.mae_kw != null && <KV k="MAE" v={`${num(rep.mae_kw, 3)} kW`} />}
                    {rep.r2 != null && <KV k="R²" v={num(rep.r2, 4)} />}
                    {rep.cv_mae_kw != null && <KV k="CV MAE (time-series)" v={`${num(rep.cv_mae_kw, 3)} kW`} />}
                    {rep.wind_mae_kw != null && <KV k="Wind MAE" v={`${num(rep.wind_mae_kw, 3)} kW`} />}
                    {rep.solar_mae_kw != null && <KV k="Solar MAE" v={`${num(rep.solar_mae_kw, 3)} kW`} />}
                    {rep.target_description && (
                      <div className="tiny muted mt-2">{rep.target_description}</div>
                    )}
                  </div>
                )
              ))}
            </Card>

            <Card title="Active recommendations and their drivers" icon={<Lightbulb size={14} />}>
              {recs.loading ? <Loading rows={3} />
                : !(recs.data || []).length ? <Empty message="No active recommendations." />
                  : (recs.data || []).map((r) => (
                    <div className={`rec-card u-${r.urgency}`} key={r.id}>
                      <div className="row gap-2 wrap mb-2">
                        <Tag kind={urgencyTag(r.urgency)}>{r.urgency}</Tag>
                        <Tag kind="neutral">{r.category.replace(/_/g, ' ')}</Tag>
                        {r.confidence != null && (
                          <span className="tiny muted">confidence {pct(r.confidence * 100, 0)}</span>
                        )}
                      </div>
                      <div className="rec-title">{r.title}</div>
                      <div className="rec-action"><strong>Action ·</strong> {r.action}</div>
                      <div className="rec-rationale">{r.rationale}</div>
                      <WhyThisAction reasons={r.reasons} compact />
                      {r.drivers?.length > 0 && (
                        <div className="row gap-2 wrap mt-3">
                          {r.drivers.map((dr, i) => (
                            <span className="driver-chip" key={i}>
                              {dr.factor}: <span className="dv">{dr.value}</span>
                              {dr.impact && <span className="faint"> ({dr.impact})</span>}
                            </span>
                          ))}
                        </div>
                      )}
                      {r.counterfactual && (
                        <div className="tiny muted mt-2"><em>{r.counterfactual}</em></div>
                      )}
                    </div>
                  ))}
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
