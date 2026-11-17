import { useEffect, useState } from 'react'

const QUALITY_LABELS = { Clear: '清晰', Acceptable: '可接受', Poor: '淘汰' }
const STAGE_LABELS = { STAGE_1: 'Stage 1 · 慢速', STAGE_2: 'Stage 2 · 接近正常语速', STAGE_3: 'Stage 3 · 正常语速' }
const STAGES = ['STAGE_1', 'STAGE_2', 'STAGE_3']
const DISPOSITION_LABELS = {
  ELIGIBLE: '合格可入库',
  PENDING_REVIEW: '待人工确认',
  PENDING: '待处理',
  REJECTED: '不合格',
  RETRYABLE_FAILED: '可重试失败',
  UNAVAILABLE: '已确认无法获取',
  INGESTED: '已入库',
  OUT_OF_SCOPE: '本次范围外',
}
const REASON_LABELS = {
  ACCENT_EXCLUDED_EN_US: '英音优先：en-US 直接排除',
  ACCENT_UNVERIFIED: '口音未验证',
  LICENSE_UNVERIFIED: '授权未确认',
  CEFR_UNVERIFIED: 'CEFR 未标注',
  TRANSCRIPT_UNVERIFIED: '稿音未验证',
  TRANSCRIPT_MISSING: '缺少文字稿',
  TRANSCRIPT_INCOMPLETE: '文字稿不完整',
  TRANSCRIPT_MISMATCHED: '稿音不匹配',
  QUALITY_POOR: '音质不合格',
  DURATION_OUT_OF_SCOPE: '时长不在本次范围',
  DURATION_MISSING: '缺少时长',
  DOWNLOAD_FAILED: '下载失败（可重试）',
  ASR_FAILED: '转录失败（可重试）',
  AUDIO_UNREADABLE: '音频无法解码（可重试）',
  REVIEW_REJECTED: '人工判定不合格',
}

function reasonText(code) {
  return code ? REASON_LABELS[code] || code : ''
}

function CandidatePanel({ stage, onStageChange, onMessage, onPrepared }) {
  const [sourceState, setSourceState] = useState(null)
  const [candidates, setCandidates] = useState([])
  const [batch, setBatch] = useState(null)
  const [busy, setBusy] = useState('')
  const [platform, setPlatform] = useState('50LANGUAGES')
  const [license, setLicense] = useState('')
  const [catalogScope, setCatalogScope] = useState('')
  const [accentEvidence, setAccentEvidence] = useState('en-GB')

  const refresh = () => {
    fetch('/api/p1/source/state')
      .then((response) => response.json())
      .then(setSourceState)
      .catch(() => setSourceState(null))
    fetch('/api/p1/material-candidates')
      .then((response) => response.json())
      .then(setCandidates)
      .catch(() => setCandidates([]))
  }

  useEffect(refresh, [])

  const post = (url, body) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(async (response) => {
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail?.message || payload.detail || '操作失败')
    return payload
  })

  const activate = () => {
    setBusy('activate')
    post('/api/p1/source/activate', {
      scope_id: 'default',
      platform,
      license_reference: license,
      catalog_scope: catalogScope,
      expected_cycle: sourceState?.active_source?.cycle || 0,
    })
      .then(() => { setBusy(''); onMessage('主源已启用（需授权证据齐备）。'); refresh() })
      .catch((error) => { setBusy(''); onMessage(error.message) })
  }

  const search = () => {
    setBusy('search')
    fetch('/api/p1/material-candidates/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope_id: 'default', speed_stage: stage, max_results: 3 }),
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail?.message || payload.detail || '搜索候选失败')
        return payload
      })
      .then((payload) => {
        setBatch(payload)
        setBusy('')
        const total = payload.candidates.length
        const pending = payload.pending_review.length
        onMessage(total
          ? `发现 ${total} 个合格候选、${pending} 个待确认（淘汰统计：${JSON.stringify(payload.rejection_summary)}）`
          : `没有可入库候选，${pending} 个条目待确认或已隔离。`)
        refresh()
      })
      .catch((error) => { setBusy(''); onMessage(error.message) })
  }

  const report = () => {
    setBusy('report')
    post('/api/p1/source/report', { scope_id: 'default' })
      .then(() => { setBusy(''); onMessage('完成报告已生成。'); refresh() })
      .catch((error) => { setBusy(''); onMessage(error.message) })
  }

  const confirmSwitch = () => {
    const reportId = sourceState?.last_report?.report_id
    if (!reportId) { onMessage('请先生成完成报告。'); return }
    setBusy('switch')
    post('/api/p1/source/confirm-switch', {
      scope_id: 'default',
      report_id: reportId,
      next_platform: sourceState?.next_platform || '',
      license_reference: license,
      catalog_scope: catalogScope,
      acknowledge_pending: true,
    })
      .then(() => { setBusy(''); onMessage('已确认切换主源。'); refresh() })
      .catch((error) => { setBusy(''); onMessage(error.message) })
  }

  const review = (candidateId, decision) => {
    post(`/api/p1/material-candidates/${candidateId}/review`, {
      scope_id: 'default',
      decision,
      accent: decision === 'APPROVE' ? accentEvidence : null,
      transcript_verified: decision === 'APPROVE' ? true : null,
      reviewer: 'USER',
    })
      .then(() => { onMessage(decision === 'APPROVE' ? '已确认该候选。' : '已判定该候选不合格。'); refresh() })
      .catch((error) => onMessage(error.message))
  }

  const prepare = (candidateId) => {
    const idempotencyKey = `${candidateId}-${Date.now()}`
    post(`/api/p1/material-candidates/${candidateId}/prepare`, {
      scope_id: 'default',
      idempotency_key: idempotencyKey,
    })
      .then((payload) => { onMessage('候选已入正式库。'); refresh(); onPrepared(payload.material_id) })
      .catch((error) => onMessage(error.message))
  }

  const active = sourceState?.active_source
  const reportData = sourceState?.last_report
  const pendingReview = candidates.filter((item) => item.ingestion_status === 'PENDING_REVIEW')
  const eligible = candidates.filter((item) => item.ingestion_status === 'ELIGIBLE')
  const blocked = candidates.filter((item) => ['REJECTED', 'RETRYABLE_FAILED', 'UNAVAILABLE'].includes(item.ingestion_status))

  return (
    <div className="weekly-panel">
      <p className="reading-title">主源治理（P1）——先确认主源，候选经审核后才入库</p>
      <div className="weekly-meta">
        <span>当前主源：{active ? `${active.platform} · 周期 ${active.cycle}` : '未启用'}</span>
        <span>下一平台：{sourceState?.next_platform || '—'}</span>
        <span>候选清单：{candidates.length} 条</span>
      </div>

      <div className="weekly-meta">
        <label>难度阶段
          <select value={stage} onChange={(event) => onStageChange(event.target.value)}>
            {STAGES.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <span>{STAGE_LABELS[stage]}</span>
        <span>保留完整原课音频；时长不再设 15–20 分钟硬门槛</span>
      </div>

      <div className="weekly-item">
        <span>授权</span>
        <input placeholder="平台 ID（如 50LANGUAGES）" value={platform} onChange={(event) => setPlatform(event.target.value)} />
        <input placeholder="授权证据/许可编号" value={license} onChange={(event) => setLicense(event.target.value)} />
        <input placeholder="目录范围（快照/页码）" value={catalogScope} onChange={(event) => setCatalogScope(event.target.value)} />
        <button className="secondary" onClick={activate} disabled={busy === 'activate' || Boolean(active)}>启用主源</button>
      </div>

      <div className="dictation-actions">
        <button className="primary" onClick={search} disabled={!active || busy === 'search'}>
          {busy === 'search' ? '正在发现候选（含音质检测与转录）…' : '发现候选素材'}
        </button>
        <button className="secondary" onClick={report} disabled={!active || busy === 'report'}>生成完成报告</button>
        <button className="secondary" onClick={confirmSwitch} disabled={!active || busy === 'switch'}>
          确认切换下一平台
        </button>
      </div>

      {reportData && (
        <div className="weekly-meta">
          <span>报告：{reportData.completion_status}</span>
          <span>已发现 {reportData.discovered} / {reportData.known_total ?? '未知'}</span>
          <span>剩余 {reportData.remaining_items}</span>
          <span>待确认 {(reportData.pending_manual_items || []).length}</span>
          <span>可重试 {(reportData.retryable_failures || []).length}</span>
        </div>
      )}

      {eligible.length > 0 && (
        <div className="weekly-items">
          <p className="muted">合格候选（按平台课程顺序，只有下一项可入库）</p>
          {eligible.map((candidate) => (
            <div className="weekly-item" key={candidate.candidate_id}>
              <span>▶</span>
              <div className="candidate-info">
                <strong>{candidate.title}</strong>
                <p className="muted">
                  {candidate.cefr_level || 'CEFR 未标'} · {candidate.accent || '口音未知'}
                  {' · '}{Math.round(candidate.duration_seconds)}s · {QUALITY_LABELS[candidate.audio_quality] || candidate.audio_quality}
                  {' · '}Transcript {candidate.transcript_status}
                </p>
              </div>
              <button className="primary" onClick={() => prepare(candidate.candidate_id)}>选择并准备</button>
            </div>
          ))}
        </div>
      )}

      {pendingReview.length > 0 && (
        <div className="weekly-items">
          <p className="muted">待人工确认（审核证据齐备后才可入库）</p>
          <div className="weekly-item">
            <span>口音</span>
            <input value={accentEvidence} onChange={(event) => setAccentEvidence(event.target.value)} placeholder="确认口音，如 en-GB" />
          </div>
          {pendingReview.map((candidate) => (
            <div className="weekly-item" key={candidate.candidate_id}>
              <span>?</span>
              <div className="candidate-info">
                <strong>{candidate.title}</strong>
                <p className="muted">
                  {reasonText(candidate.failure_code) || '证据不足'} · {candidate.cefr_level || 'CEFR 未标'}
                  {' · '}{candidate.accent || '口音未知'} · {Math.round(candidate.duration_seconds)}s
                </p>
              </div>
              <button className="secondary" onClick={() => review(candidate.candidate_id, 'APPROVE')}>确认</button>
              <button className="secondary" onClick={() => review(candidate.candidate_id, 'REJECT')}>判为不合格</button>
            </div>
          ))}
        </div>
      )}

      {blocked.length > 0 && (
        <div className="weekly-items">
          <p className="muted">隔离 / 失败（保留原因，不进入正式库与周测新题池）</p>
          {blocked.map((candidate) => (
            <div className="weekly-item" key={candidate.candidate_id}>
              <span>×</span>
              <div className="candidate-info">
                <strong>{candidate.title}</strong>
                <p className="muted">
                  {DISPOSITION_LABELS[candidate.ingestion_status] || candidate.ingestion_status}
                  {' · '}{reasonText(candidate.failure_code)}
                  {' · '}尝试 {candidate.attempt_count} 次
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {batch && Object.keys(batch.rejection_summary || {}).length > 0 && (
        <p className="muted">本次淘汰统计：{JSON.stringify(batch.rejection_summary)}</p>
      )}
    </div>
  )
}

export default CandidatePanel
