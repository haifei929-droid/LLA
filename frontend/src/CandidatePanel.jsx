import { useState } from 'react'

const QUALITY_LABELS = { Clear: '清晰', Acceptable: '可接受', Poor: '淘汰' }
const STAGE_LABELS = { STAGE_1: 'Stage 1 · VOA 慢速', STAGE_2: 'Stage 2 · 接近正常语速', STAGE_3: 'Stage 3 · 正常语速' }

function CandidatePanel({ stage, onStageChange, onMessage, onPrepared }) {
  const [batch, setBatch] = useState(null)
  const [busy, setBusy] = useState(false)

  const search = () => {
    setBusy(true)
    fetch('/api/p1/material-candidates/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope_id: 'default',
        speed_stage: stage,
        target_duration_min: 15,
        target_duration_max: 20,
        max_results: 3,
      }),
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail?.message || payload.detail || '搜索候选失败')
        return payload
      })
      .then((payload) => {
        setBatch(payload)
        setBusy(false)
        const total = payload.candidates.length
        onMessage(total
          ? `搜索完成：${total} 个候选（淘汰统计：${JSON.stringify(payload.rejection_summary)}）`
          : '没有符合 15–20 分钟、清晰音质要求的候选。')
      })
      .catch((error) => { setBusy(false); onMessage(error.message) })
  }

  const prepare = (candidateId) => {
    const idempotencyKey = `${candidateId}-${Date.now()}`
    fetch(`/api/p1/material-candidates/${candidateId}/prepare`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope_id: 'default', idempotency_key: idempotencyKey }),
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail?.message || payload.detail || '准备失败')
        return payload
      })
      .then((payload) => {
        onMessage('候选已准备完成，正式素材已创建。')
        onPrepared(payload.material_id)
      })
      .catch((error) => onMessage(error.message))
  }

  return (
    <div className="weekly-panel">
      <p className="reading-title">候选素材（P1）——先搜索候选，确认后才创建正式素材</p>
      <div className="weekly-meta">
        <label>难度阶段
          <select value={stage} onChange={(event) => onStageChange(event.target.value)}>
            <option value="STAGE_1">STAGE_1</option>
            <option value="STAGE_2">STAGE_2</option>
            <option value="STAGE_3">STAGE_3</option>
          </select>
        </label>
        <span>{STAGE_LABELS[stage]}</span>
        <span>时长区间 15–20 分钟（硬约束）</span>
      </div>
      <div className="dictation-actions">
        <button className="primary" onClick={search} disabled={busy}>{busy ? '正在搜索候选（含音质检测与转录，可能需数分钟）…' : '搜索候选素材'}</button>
      </div>

      {batch && (
        <div className="weekly-items">
          {batch.candidates.length ? batch.candidates.map((candidate) => (
            <div className="weekly-item" key={candidate.candidate_id}>
              <span>▶</span>
              <div className="candidate-info">
                <strong>{candidate.title}</strong>
                <p className="muted">
                  {candidate.duration_seconds}s · {QUALITY_LABELS[candidate.audio_quality] || candidate.audio_quality}
                  {' · '}Transcript {candidate.transcript_status} · {candidate.speech_rate_wpm} wpm
                </p>
              </div>
              <button className="primary" onClick={() => prepare(candidate.candidate_id)}>选择并准备</button>
            </div>
          )) : <p className="muted">无候选。可调整时长区间或稍后重试。</p>}
          {Object.keys(batch.rejection_summary || {}).length > 0 && (
            <p className="muted">淘汰统计：{JSON.stringify(batch.rejection_summary)}</p>
          )}
        </div>
      )}
    </div>
  )
}

export default CandidatePanel
