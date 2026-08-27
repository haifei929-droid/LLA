import { useEffect, useState } from 'react'

const STAGE_LABELS = { STAGE_1: 'VOA Slow', STAGE_2: '接近正常语速', STAGE_3: '正常语速' }

function P2Dashboard({ onMessage }) {
  const [dashboard, setDashboard] = useState(null)
  const [memory, setMemory] = useState(null)
  const [history, setHistory] = useState(null)
  const [config, setConfig] = useState(null)
  const [form, setForm] = useState({ short_days: 14, long_days: 56, min_episodes: 3, min_dates: 2 })
  const [rangeStart, setRangeStart] = useState('')
  const [rangeEnd, setRangeEnd] = useState('')

  const refresh = () => {
    const params = new URLSearchParams({ scope_id: 'default', granularity: 'week' })
    if (rangeStart) params.set('range_start', rangeStart)
    if (rangeEnd) params.set('range_end', rangeEnd)
    fetch(`/api/p2/dashboard?${params}`)
      .then((response) => response.json())
      .then(setDashboard)
      .catch(() => onMessage('仪表盘读取失败。'))
    fetch('/api/p2/memory?scope_id=default')
      .then((response) => response.json())
      .then(setMemory)
      .catch(() => onMessage('Memory 读取失败。'))
    fetch('/api/p2/difficulty/history?scope_id=default')
      .then((response) => response.json())
      .then(setHistory)
      .catch(() => onMessage('难度历史读取失败。'))
  }

  useEffect(() => { refresh() }, [])

  const backfill = () => {
    fetch('/api/p2/memory/backfill?scope_id=default', { method: 'POST' })
      .then((response) => response.json())
      .then((payload) => { onMessage(`回填完成：新增 ${payload.episodes_created} 个识别会话。`); refresh() })
      .catch((error) => onMessage(error.message))
  }

  const saveConfig = () => {
    fetch('/api/p2/memory/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope_id: 'default', ...form }),
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail?.message || '配置保存失败')
        return payload
      })
      .then((payload) => { setConfig(payload); onMessage(`阈值配置已保存（版本 ${payload.config_version}）。`); refresh() })
      .catch((error) => onMessage(error.message))
  }

  const loadConfig = () => {
    fetch('/api/p2/memory/config?scope_id=default')
      .then((response) => response.json())
      .then((payload) => {
        setConfig(payload)
        if (payload.configured) {
          setForm({
            short_days: payload.short_days, long_days: payload.long_days,
            min_episodes: payload.min_episodes, min_dates: payload.min_dates,
          })
        }
      })
      .catch(() => onMessage('配置读取失败。'))
  }

  useEffect(() => { loadConfig() }, [])

  const runSuggestions = () => {
    fetch('/api/p2/memory/suggestions?scope_id=default')
      .then((response) => response.json())
      .then((payload) => onMessage(`建议生成：${payload.generated} 条（${payload.reason}）。`))
      .catch((error) => onMessage(error.message))
  }

  const suggestionAction = (action) => {
    fetch('/api/p2/memory/suggestions/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope_id: 'default', action }),
    })
      .then((response) => response.json())
      .then(() => onMessage(`建议操作 ${action} 已执行。`))
      .catch((error) => onMessage(error.message))
  }

  const summary = dashboard?.summary
  const curve = dashboard?.trend?.first_comprehension_curve
  const timeSeries = dashboard?.trend?.time_series || []
  const weekly = dashboard?.trend?.weekly_dictation || []
  const reading = dashboard?.trend?.reading_practice
  const streak = dashboard?.trend?.difficulty_streak
  const memoryTargets = memory?.targets || []
  const historyEvents = history?.events || []

  return (
    <div className="p2-dashboard">
      <p className="reading-title">长期训练仪表盘（P2）· 只读视图</p>

      <div className="weekly-meta">
        <label>开始日期 <input type="date" value={rangeStart} onChange={(e) => setRangeStart(e.target.value)} /></label>
        <label>结束日期 <input type="date" value={rangeEnd} onChange={(e) => setRangeEnd(e.target.value)} /></label>
        <button className="secondary" onClick={refresh}>应用范围</button>
        <span>粒度：周</span>
      </div>

      {summary && (
        <div className="dimension-blocks">
          <div className="dimension-block lv-pass"><strong>累计有效学习</strong><span>{summary.total_valid_hours} 小时</span><small>范围内：{summary.range_hours} 小时</small></div>
          <div className="dimension-block lv-pass"><strong>当前难度阶段</strong><span>{STAGE_LABELS[summary.current_stage] || summary.current_stage}</span><small>连续通过 {streak?.consecutive_pass_weeks ?? 0} 周</small></div>
          <div className="dimension-block lv-pass"><strong>周测 Gate</strong><span>{summary.current_gate_state || '无记录'}</span><small>建议功能：{summary.review_suggestions_enabled ? '启用' : '停用'}</small></div>
        </div>
      )}

      <div className="p2-section">
        <p className="feedback-title">首次理解度曲线（FIRST 映射 15/40/60/85）</p>
        {curve?.points?.length ? (
          <div className="curve-bars">
            {curve.points.map((point) => (
              <div className="curve-bar" key={point.period} title={`${point.period}: ${point.mapped_score}（${point.sample_count} 篇）`}>
                <div className="curve-fill" style={{ height: `${Math.max(4, point.mapped_score)}%` }} />
                <small>{point.period}</small>
              </div>
            ))}
          </div>
        ) : <p className="muted">暂无首次理解记录（样本数 {curve?.sample_count ?? 0}）。</p>}
        {curve && <p className="muted">样本数 {curve.sample_count} · 映射版本 {curve.mapping_version}</p>}
      </div>

      <div className="p2-section">
        <p className="feedback-title">有效学习时长趋势（小时 / 周）</p>
        {timeSeries.length ? (
          <div className="curve-bars">
            {timeSeries.map((point) => (
              <div className="curve-bar" key={point.period} title={`${point.period}: ${point.value}h（${point.sample_count} 条日志）`}>
                <div className="curve-fill" style={{ height: `${Math.min(100, point.value / Math.max(1, ...timeSeries.map((p) => p.value)) * 100)}%` }} />
                <small>{point.period}</small>
              </div>
            ))}
          </div>
        ) : <p className="muted">暂无时长数据。</p>}
      </div>

      <div className="p2-section">
        <p className="feedback-title">周测成绩与 Gate（独立展示，不覆盖原始分）</p>
        {weekly.length ? weekly.map((point) => (
          <div className="weekly-item" key={point.period}>
            <span>W</span>
            <div className="candidate-info">
              <strong>{point.period}</strong>
              <p className="muted">听写 {point.value}% · {point.gate_status}{point.reinforcement_status !== 'NOT_REQUIRED' ? ` · ${point.reinforcement_status}` : ''}</p>
            </div>
          </div>
        )) : <p className="muted">暂无周测记录。</p>}
      </div>

      {reading && (
        <div className="p2-section">
          <p className="feedback-title">朗读练习三维分布（独立判定，不平均）</p>
          <div className="dimension-blocks">
            <div className="dimension-block lv-close"><strong>语速</strong><span>{JSON.stringify(reading.dimension_distributions.speed)}</span></div>
            <div className="dimension-block lv-close"><strong>停顿</strong><span>{JSON.stringify(reading.dimension_distributions.pause)}</span></div>
            <div className="dimension-block lv-close"><strong>重音</strong><span>{JSON.stringify(reading.dimension_distributions.stress)}</span></div>
          </div>
        </div>
      )}

      <div className="p2-section">
        <p className="feedback-title">听写记忆（P2 四层：目标/出现/识别会话/聚合）</p>
        <div className="dictation-actions">
          <button className="secondary" onClick={backfill}>从听写记录回填识别会话</button>
          <button className="secondary" onClick={runSuggestions}>生成复习建议</button>
          <button className="secondary" onClick={() => suggestionAction('batch_pause')}>暂停/恢复建议</button>
          <button className="secondary" onClick={() => suggestionAction('restore')}>恢复默认建议</button>
        </div>
        {memory && memory.classification_status === 'UNCONFIGURED' && (
          <p className="notice">尚未配置阈值——分类返回 UNCONFIGURED，不使用隐藏默认值。</p>
        )}
        <div className="weekly-meta">
          <label>短窗(天)<input type="number" value={form.short_days} onChange={(e) => setForm({ ...form, short_days: Number(e.target.value) })} /></label>
          <label>长窗(天)<input type="number" value={form.long_days} onChange={(e) => setForm({ ...form, long_days: Number(e.target.value) })} /></label>
          <label>最少会话<input type="number" value={form.min_episodes} onChange={(e) => setForm({ ...form, min_episodes: Number(e.target.value) })} /></label>
          <label>最少日期<input type="number" value={form.min_dates} onChange={(e) => setForm({ ...form, min_dates: Number(e.target.value) })} /></label>
          <button className="primary" onClick={saveConfig}>保存阈值配置{config?.config_version ? `（v${config.config_version}）` : ''}</button>
        </div>
        {memoryTargets.length ? (
          <div className="weekly-items">
            {memoryTargets.map((target) => (
              <div className="weekly-item" key={target.target}>
                <span>◉</span>
                <div className="candidate-info">
                  <strong>{target.target} · {target.difficulty_classification}</strong>
                  <p className="muted">
                    {target.qualifying_episodes} 会话 / {target.distinct_dates} 天 · 平均首次听出 {target.average_first_correct_listen ?? '—'}
                    {target.hint_count ? ` · 提示 ${target.hint_count}` : ''}{target.reveal_count ? ` · Reveal ${target.reveal_count}` : ''}
                  </p>
                  <p className="muted">阈值：短 {target.thresholds_applied?.short_days} 天 / 长 {target.thresholds_applied?.long_days} 天 / 会话 {target.thresholds_applied?.min_episodes} / 日期 {target.thresholds_applied?.min_dates}（v{target.thresholds_applied?.config_version}）</p>
                </div>
              </div>
            ))}
          </div>
        ) : <p className="muted">暂无记忆目标。先执行「回填识别会话」。</p>}
      </div>

      <div className="p2-section">
        <p className="feedback-title">难度进阶历史（不可变事件流）</p>
        <div className="weekly-items">
          {historyEvents.length ? historyEvents.map((event) => (
            <div className="weekly-item" key={event.event_id}>
              <span>◆</span>
              <div className="candidate-info">
                <strong>{event.event_type}</strong>
                <p className="muted">
                  {event.occurred_at.slice(0, 16).replace('T', ' ')} · {event.stage_before || '—'} → {event.stage_after || '—'} · {event.actor}
                  {event.reason ? ` · ${event.reason}` : ''}
                </p>
              </div>
            </div>
          )) : <p className="muted">暂无难度历史事件。</p>}
        </div>
        <div className="dictation-actions">
          <button className="secondary" onClick={() => fetch('/api/p2/difficulty/downgrade/suggest?scope_id=default', { method: 'POST' }).then((r) => r.json()).then((p) => onMessage(`降级建议检查：${p.suggested ? '触发' : '未触发'}（${p.reason}）`)).catch((e) => onMessage(e.message))}>检查降级建议</button>
          <button className="secondary" onClick={() => fetch('/api/p2/difficulty/downgrade/request?scope_id=default', { method: 'POST' }).then((r) => r.json()).then((p) => onMessage('降级请求已记录')).catch((e) => onMessage(e.message))}>请求降级</button>
          <button className="secondary" onClick={() => fetch('/api/p2/difficulty/downgrade/confirm?scope_id=default', { method: 'POST' }).then((r) => r.json()).then((p) => onMessage(`降级已确认：${p.stage_before} → ${p.stage_after}`)).catch((e) => onMessage(e.message))}>确认降级</button>
        </div>
      </div>
    </div>
  )
}

export default P2Dashboard
