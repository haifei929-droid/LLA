import { useEffect, useRef, useState } from 'react'

const BLOCKER_LABELS = {
  TRANSCRIPT_MISSING: '缺少文字稿',
  ALIGNMENT_MISSING: '尚未对齐',
  ALIGNMENT_STALE: '对齐已过期，需要重新对齐',
  ALIGNMENT_INCOMPLETE: '存在未匹配的句子',
  ALIGNMENT_LOW_COVERAGE: '对齐覆盖率不足',
}

const STATUS_CLASS = {
  AUDIO_ONLY: 'is-muted',
  NEEDS_REVIEW: 'is-warning',
  ALIGNED: 'is-info',
  APPROVED: 'is-ok',
  TRAINABLE: 'is-success',
}

const COUNT_FIELDS = [
  ['audio_files', '音频'],
  ['transcripts', '文稿'],
  ['aligned', '已对齐'],
  ['pending_review', '待审核'],
  ['ingested', '已入库'],
  ['trainable', '可选'],
]

const EMPTY_APPROVAL = {
  license: '',
  accentConfirmed: false,
  transcriptVerified: false,
  qualityOk: false,
  confirmed: false,
}

function blockerText(code) {
  return BLOCKER_LABELS[code] || code
}

function formatBytes(bytes) {
  if (!Number.isFinite(Number(bytes))) return '—'
  const value = Number(bytes)
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  if (value >= 1024) return `${Math.round(value / 1024)} KB`
  return `${value} B`
}

function formatDuration(seconds) {
  if (!Number.isFinite(Number(seconds)) || seconds === null) return '时长未知'
  const total = Math.round(Number(seconds))
  const minutes = Math.floor(total / 60)
  return `${minutes}:${String(total % 60).padStart(2, '0')}`
}

function formatClock(seconds) {
  if (!Number.isFinite(Number(seconds))) return '—'
  const value = Number(seconds)
  const minutes = Math.floor(value / 60)
  return `${minutes}:${(value % 60).toFixed(1).padStart(4, '0')}`
}

function coveragePercent(value) {
  if (!Number.isFinite(Number(value))) return 0
  return Math.round(Number(value) * 100)
}

function alignmentText(item) {
  const alignment = item.alignment || {}
  if (!alignment.present) return '未对齐'
  const parts = [
    `覆盖 ${coveragePercent(alignment.coverage)}%`,
    `未匹配 ${alignment.unaligned || 0} 行`,
    `共 ${alignment.lines || 0} 行`,
  ]
  if (alignment.stale) parts.unshift('已过期')
  if (alignment.model) parts.push(`模型 ${alignment.model}`)
  return parts.join(' · ')
}

function domId(itemId) {
  return String(itemId).replace(/[^A-Za-z0-9_-]/g, '-')
}

function detailFromPayload(payload, status) {
  const detail = payload && payload.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return {
      code: detail.code || `HTTP_${status}`,
      message: detail.message || JSON.stringify(detail),
    }
  }
  if (typeof detail === 'string') {
    return { code: `HTTP_${status}`, message: detail }
  }
  if (Array.isArray(detail)) {
    return {
      code: 'VALIDATION_ERROR',
      message: detail.map((entry) => entry.msg || JSON.stringify(entry)).join('；'),
    }
  }
  return { code: `HTTP_${status}`, message: '请求失败，请检查后端连接后重试。' }
}

function readJson(response) {
  return response.json().catch(() => null)
}

function MaterialImportPanel({ onStartTraining }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')

  const [uploadFile, setUploadFile] = useState(null)
  const [uploadTitle, setUploadTitle] = useState('')
  const [uploadPercent, setUploadPercent] = useState(null)
  const [fetchResult, setFetchResult] = useState(null)

  const [showActivate, setShowActivate] = useState(false)
  const [activation, setActivation] = useState({ license: '', scope: '', confirmed: false })

  const [expandedTranscript, setExpandedTranscript] = useState('')
  const [drafts, setDrafts] = useState({})
  const [approvalOpen, setApprovalOpen] = useState('')
  const [approvals, setApprovals] = useState({})
  const [alignPreviews, setAlignPreviews] = useState({})

  const fileInputRef = useRef(null)
  const xhrRef = useRef(null)

  const showError = (reason, fallbackCode = 'REQUEST_FAILED') => {
    setError({
      code: reason?.code || fallbackCode,
      message: reason?.message || '请求失败，请稍后重试。',
    })
  }

  const call = async (url, options) => {
    const response = await fetch(url, options)
    const payload = await readJson(response)
    if (!response.ok) {
      const detail = detailFromPayload(payload, response.status)
      const failure = new Error(detail.message)
      failure.code = detail.code
      throw failure
    }
    return payload
  }

  const load = () => {
    setLoading(true)
    return fetch('/api/materials/intake/items')
      .then(async (response) => {
        const payload = await readJson(response)
        if (!response.ok) {
          const detail = detailFromPayload(payload, response.status)
          const failure = new Error(detail.message)
          failure.code = detail.code
          throw failure
        }
        return payload
      })
      .then((payload) => {
        setData(payload)
        setError(null)
      })
      .catch((reason) => showError(reason, 'LOAD_FAILED'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    return () => {
      if (xhrRef.current) xhrRef.current.abort()
    }
  }, [])

  const activeSource = data?.active_source && data.active_source !== 'null' ? data.active_source : null
  const sourceKnown = Boolean(data)
  const counts = data?.counts || {}
  const band = data?.band || '—'

  const items = [...(data?.items || [])].sort((a, b) => {
    const byCourse = String(a.source_course || '').localeCompare(String(b.source_course || ''), 'en')
    if (byCourse !== 0) return byCourse
    const orderA = a.item_order == null ? Number.MAX_SAFE_INTEGER : a.item_order
    const orderB = b.item_order == null ? Number.MAX_SAFE_INTEGER : b.item_order
    if (orderA !== orderB) return orderA - orderB
    return String(a.stem || '').localeCompare(String(b.stem || ''), 'en', { numeric: true })
  })

  const activateSource = (event) => {
    event.preventDefault()
    if (busy) return
    setBusy('activate')
    setError(null)
    call('/api/materials/intake/activate-source', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        platform: '50LANGUAGES',
        license_reference: activation.license.trim(),
        catalog_scope: activation.scope.trim(),
        confirm: activation.confirmed,
      }),
    })
      .then((payload) => {
        setNotice(`主源已启用：${payload.platform || '50LANGUAGES'}（周期 ${payload.cycle || 1}）。`)
        setShowActivate(false)
        setActivation({ license: '', scope: '', confirmed: false })
        return load()
      })
      .catch((reason) => showError(reason, 'ACTIVATE_FAILED'))
      .finally(() => setBusy(''))
  }

  const stageUpload = (event) => {
    const file = event.target.files && event.target.files[0]
    setUploadFile(file || null)
    setError(null)
  }

  const uploadAudio = () => {
    if (!uploadFile || busy) return
    const suffix = uploadFile.name.slice(uploadFile.name.lastIndexOf('.')).toLowerCase()
    if (!['.mp3', '.wav', '.m4a', '.flac'].includes(suffix)) {
      showError({ code: 'UNSUPPORTED_AUDIO', message: '仅支持 MP3 / WAV / M4A / FLAC 音频。' }, 'UNSUPPORTED_AUDIO')
      return
    }
    if (uploadFile.size > 160 * 1024 * 1024) {
      showError({ code: 'FILE_TOO_LARGE', message: '音频超过 160 MB，后端会拒绝这次上传。' }, 'FILE_TOO_LARGE')
      return
    }

    const params = new URLSearchParams({ filename: uploadFile.name })
    if (uploadTitle.trim()) params.set('title', uploadTitle.trim())

    setBusy('upload')
    setUploadPercent(0)
    setError(null)
    setNotice('')

    const xhr = new XMLHttpRequest()
    xhrRef.current = xhr
    xhr.open('POST', `/api/materials/intake/upload?${params.toString()}`)
    xhr.setRequestHeader('Content-Type', uploadFile.type || 'application/octet-stream')
    xhr.upload.onprogress = (progress) => {
      if (progress.lengthComputable) {
        setUploadPercent(Math.round((progress.loaded / progress.total) * 100))
      }
    }
    xhr.onload = () => {
      let payload = null
      try {
        payload = JSON.parse(xhr.responseText)
      } catch {
        payload = null
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        setNotice(payload && payload.created === false
          ? '该音频已在暂存目录，已返回现有条目。'
          : '音频已暂存：请补充并校对文字稿，然后对齐与审核。')
        setUploadFile(null)
        setUploadTitle('')
        if (fileInputRef.current) fileInputRef.current.value = ''
        load()
      } else {
        showError(detailFromPayload(payload, xhr.status), 'UPLOAD_FAILED')
      }
      setBusy('')
      setUploadPercent(null)
      xhrRef.current = null
    }
    xhr.onerror = () => {
      showError({ code: 'NETWORK_ERROR', message: '上传失败：网络连接中断。' }, 'NETWORK_ERROR')
      setBusy('')
      setUploadPercent(null)
      xhrRef.current = null
    }
    xhr.send(uploadFile)
  }

  const fetchOfficial = () => {
    if (busy) return
    setBusy('fetch')
    setError(null)
    setFetchResult(null)
    setNotice('')
    call('/api/materials/intake/fetch-official', { method: 'POST' })
      .then((payload) => {
        setFetchResult(payload)
        setNotice('官方音频获取完成，已加入暂存列表。')
        return load()
      })
      .catch((reason) => showError(reason, 'FETCH_FAILED'))
      .finally(() => setBusy(''))
  }

  const toggleTranscript = (item) => {
    setApprovalOpen('')
    setExpandedTranscript((current) => (current === item.item_id ? '' : item.item_id))
    setDrafts((current) => (
      current[item.item_id] === undefined
        ? { ...current, [item.item_id]: item.transcript_text || '' }
        : current
    ))
  }

  const saveTranscript = (item) => {
    if (busy) return
    const text = drafts[item.item_id] || ''
    if (!text.trim()) {
      showError({ code: 'EMPTY_TRANSCRIPT', message: '文字稿不能为空。' }, 'EMPTY_TRANSCRIPT')
      return
    }
    setBusy(`transcript:${item.item_id}`)
    setError(null)
    call(`/api/materials/intake/items/${encodeURIComponent(item.item_id)}/transcript`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
      .then(() => {
        setNotice('文字稿已保存；旧对齐结果已失效，请重新对齐。')
        setExpandedTranscript('')
        setAlignPreviews((current) => {
          const next = { ...current }
          delete next[item.item_id]
          return next
        })
        return load()
      })
      .catch((reason) => showError(reason, 'TRANSCRIPT_FAILED'))
      .finally(() => setBusy(''))
  }

  const alignItem = (item) => {
    if (busy) return
    setBusy(`align:${item.item_id}`)
    setError(null)
    call(`/api/materials/intake/items/${encodeURIComponent(item.item_id)}/align`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: 'small' }),
    })
      .then((payload) => {
        const alignment = payload.alignment || {}
        setAlignPreviews((current) => ({
          ...current,
          [item.item_id]: alignment.lines_preview || [],
        }))
        setNotice(alignment.unaligned
          ? `对齐完成，但有 ${alignment.unaligned} 行未匹配，请修改文字稿后重新对齐。`
          : `对齐完成：覆盖 ${coveragePercent(alignment.coverage)}%，共 ${alignment.lines || 0} 行。`)
        return load()
      })
      .catch((reason) => showError(reason, 'ALIGN_FAILED'))
      .finally(() => setBusy(''))
  }

  const toggleApproval = (item) => {
    setExpandedTranscript('')
    setApprovalOpen((current) => (current === item.item_id ? '' : item.item_id))
    setApprovals((current) => (
      current[item.item_id] === undefined ? { ...current, [item.item_id]: { ...EMPTY_APPROVAL } } : current
    ))
  }

  const approvalDraft = (itemId) => ({ ...EMPTY_APPROVAL, ...(approvals[itemId] || {}) })

  const updateApproval = (itemId, patch) => {
    setApprovals((current) => ({
      ...current,
      [itemId]: { ...EMPTY_APPROVAL, ...(current[itemId] || {}), ...patch },
    }))
  }

  const submitApproval = (event, item) => {
    event.preventDefault()
    if (busy) return
    const draft = approvalDraft(item.item_id)
    const ready = item.can_approve
      && draft.license === 'CLEARED'
      && draft.accentConfirmed
      && draft.transcriptVerified
      && draft.qualityOk
      && draft.confirmed
    if (!ready) return

    setBusy(`approve:${item.item_id}`)
    setError(null)
    call(`/api/materials/intake/items/${encodeURIComponent(item.item_id)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        accent: 'en-GB',
        license_status: draft.license,
        transcript_verified: draft.transcriptVerified,
        quality_ok: draft.qualityOk,
        confirm: draft.confirmed,
      }),
    })
      .then((payload) => {
        setNotice(payload.already_approved
          ? '该条目此前已审核入库，本次返回既有结果。'
          : `审核完成，已入库：${payload.material_id || item.material_id}`)
        setApprovalOpen('')
        return load()
      })
      .catch((reason) => showError(reason, 'APPROVE_FAILED'))
      .finally(() => setBusy(''))
  }

  const startTraining = (item) => {
    if (!item.can_train || !item.material_id || !onStartTraining) return
    setError(null)
    onStartTraining(item.material_id)
  }

  const renderError = error && (
    <div className="intake-error" role="alert" aria-live="assertive">
      <div className="intake-error-body">
        <code className="intake-error-code">{error.code}</code>
        <span>{error.message}</span>
      </div>
      <button className="intake-dismiss" type="button" onClick={() => setError(null)} aria-label="关闭错误提示">×</button>
    </div>
  )

  const renderNotice = notice && (
    <div className="intake-notice" role="status" aria-live="polite">
      <span>{notice}</span>
      <button className="intake-dismiss" type="button" onClick={() => setNotice('')} aria-label="关闭提示">×</button>
    </div>
  )

  return (
    <div className="intake-panel" aria-busy={Boolean(busy)}>
      {renderError}
      {renderNotice}

      <section className="intake-status">
        <div className="intake-head">
          <div>
            <p className="eyebrow">素材入口</p>
            <h3>选音频 → 写稿 → 对齐 → 审核 → 入池</h3>
            <p className="muted">
              本地音频或官方音频包先进入暂存目录；文字稿、对齐与审核都在本页完成。
              刷新后状态直接读取后端暂存数据。
            </p>
          </div>
          <span className="intake-band">档位 {band}</span>
        </div>

        <div className="intake-source">
          <div>
            <span className="intake-source-label">当前主源</span>
            <strong>{sourceKnown ? (activeSource || '未启用') : (loading ? '读取中…' : '读取失败')}</strong>
            <p className="intake-hint">
              {!sourceKnown
                ? (loading ? '正在读取当前主源。' : '暂时无法读取主源，请按上方错误提示重试。')
                : (activeSource
                  ? '获取官方音频与候选素材只使用这个主源。'
                  : '未启用主源时不能获取官方音频；请先确认授权证据与目录范围。')}
            </p>
          </div>
          {sourceKnown && !activeSource && (
            <button
              className="secondary"
              type="button"
              aria-expanded={showActivate}
              onClick={() => setShowActivate((current) => !current)}
            >
              {showActivate ? '取消启用' : '启用 50LANGUAGES'}
            </button>
          )}
        </div>

        {showActivate && sourceKnown && !activeSource && (
          <form className="intake-activate" onSubmit={activateSource}>
            <p className="intake-hint">主源只启用 50LANGUAGES；启用后需遵守既有主源治理规则，不能静默切换。</p>
            <label className="intake-field">
              授权证据 license_reference
              <input
                required
                value={activation.license}
                onChange={(event) => setActivation({ ...activation, license: event.target.value })}
                placeholder="授权编号或官方许可链接"
              />
            </label>
            <label className="intake-field">
              目录范围 catalog_scope
              <input
                required
                value={activation.scope}
                onChange={(event) => setActivation({ ...activation, scope: event.target.value })}
                placeholder="例如 book2-en-uk"
              />
            </label>
            <label className="intake-check">
              <input
                type="checkbox"
                checked={activation.confirmed}
                onChange={(event) => setActivation({ ...activation, confirmed: event.target.checked })}
              />
              我确认已获得该来源的授权，并同意将其设为唯一主源
            </label>
            <button
              className="primary"
              type="submit"
              disabled={Boolean(busy) || !activation.license.trim() || !activation.scope.trim() || !activation.confirmed}
            >
              {busy === 'activate' ? '启用中…' : '确认启用'}
            </button>
          </form>
        )}

        <dl className="intake-counts">
          {COUNT_FIELDS.map(([key, label]) => (
            <div className="intake-count" key={key}>
              <dt>{label}</dt>
              <dd>{data ? (counts[key] ?? 0) : '—'}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="intake-toolbar" aria-label="素材工具条">
        <div className="intake-upload-box">
          <div className="intake-upload-row">
            <input
              ref={fileInputRef}
              id="intake-audio-file"
              className="intake-file-input"
              type="file"
              accept=".mp3,.wav,.m4a,.flac"
              onChange={stageUpload}
              disabled={busy === 'upload'}
            />
            <label className={`secondary intake-file-label${busy === 'upload' ? ' is-disabled' : ''}`} htmlFor="intake-audio-file">
              {uploadFile ? '重新选择音频' : '选择本地音频上传'}
            </label>
            <span className="intake-file-name">
              {uploadFile
                ? `${uploadFile.name} · ${formatBytes(uploadFile.size)}`
                : '支持 MP3 / WAV / M4A / FLAC，最大 160 MB'}
            </span>
            <button className="primary" type="button" onClick={uploadAudio} disabled={!uploadFile || Boolean(busy)}>
              {busy === 'upload'
                ? (uploadPercent == null ? '上传中…' : `上传中 ${uploadPercent}%`)
                : '上传'}
            </button>
          </div>
          {busy === 'upload' && (
            <div
              className="intake-progress"
              role="progressbar"
              aria-label="音频上传进度"
              aria-valuemin="0"
              aria-valuemax="100"
              aria-valuenow={uploadPercent ?? undefined}
            >
              <div className="intake-progress-fill" style={{ width: `${uploadPercent ?? 0}%` }} />
            </div>
          )}
          <input
            className="intake-title-input"
            value={uploadTitle}
            onChange={(event) => setUploadTitle(event.target.value)}
            placeholder="标题（可选，默认使用文件名）"
            aria-label="音频标题（可选）"
            disabled={busy === 'upload'}
          />
        </div>

        <div className="intake-fetch-box">
          <div className="intake-fetch-row">
            <button
              className="secondary"
              type="button"
              onClick={fetchOfficial}
              disabled={Boolean(busy) || !activeSource}
              title={activeSource ? undefined : '请先启用主源'}
            >
              {busy === 'fetch' ? '正在获取官方音频…' : '获取官方音频'}
            </button>
            <span className="intake-hint">
              仅下载策略允许的官方音频包；文字稿仍需准备与人工校对，不会自动抓取。
            </span>
          </div>
          {fetchResult && (
            <div className="intake-result" role="status">
              <strong>官方音频获取完成</strong>
              <div className="intake-result-grid">
                <span>下载 {formatBytes(fetchResult.download?.bytes)}</span>
                <span>解包 {fetchResult.extracted ?? 0} 个音频</span>
                <span>{fetchResult.download?.cached ? '命中本地缓存' : '本次联网下载'}</span>
              </div>
              {fetchResult.quarantined_cache && <p className="intake-hint">旧缓存损坏，已隔离留证并重新下载。</p>}
              <p className="intake-hint">{fetchResult.note || '音频已暂存；文字稿仍需准备与校对。'}</p>
            </div>
          )}
        </div>
      </section>

      <section className="intake-list-section" aria-label="暂存素材列表">
        <div className="intake-list-head">
          <div>
            <strong>暂存素材（{items.length}）</strong>
            <p className="intake-hint">按课程与课程内序号排列；只有通过审核并进入当前档位可选池的条目才能开始训练。</p>
          </div>
          <button className="secondary" type="button" onClick={load} disabled={loading}>
            {loading ? '刷新中…' : '刷新列表'}
          </button>
        </div>

        {loading && !data && <p className="intake-hint">正在读取后端暂存目录…</p>}

        {!loading && !data && (
          <div className="intake-empty">
            <strong>素材列表暂时不可用</strong>
            <p>请先处理上方错误，再刷新列表。</p>
            <button className="primary" type="button" onClick={load}>重新加载</button>
          </div>
        )}

        {data && items.length === 0 && (
          <div className="intake-empty">
            <strong>还没有暂存音频</strong>
            <p>选择本地音频上传，或在启用主源后获取官方音频。列表来自后端暂存目录，刷新页面不会丢失。</p>
          </div>
        )}

        {items.length > 0 && (
          <ul className="intake-items">
            {items.map((item) => {
              const draft = drafts[item.item_id] || ''
              const lineCount = draft.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).length
              const approval = approvalDraft(item.item_id)
              const preview = alignPreviews[item.item_id]
              const safeId = domId(item.item_id)
              const canSubmitApproval = item.can_approve
                && approval.license === 'CLEARED'
                && approval.accentConfirmed
                && approval.transcriptVerified
                && approval.qualityOk
                && approval.confirmed
                && !busy

              return (
                <li className="intake-item" key={item.item_id}>
                  <div className="intake-item-head">
                    <div className="intake-item-info">
                      <div className="intake-item-title">
                        <strong>{item.title}</strong>
                        <span className={`intake-badge ${STATUS_CLASS[item.status] || 'is-muted'}`}>
                          {item.status_label || item.status}
                        </span>
                      </div>
                      <p className="intake-item-meta">
                        <span>{item.audio}</span>
                        <span>·</span>
                        <span>{formatDuration(item.duration_seconds)}</span>
                        <span>·</span>
                        <span>{item.source_course || '未分课程'}{item.item_order != null ? ` #${item.item_order}` : ''}</span>
                        <span>·</span>
                        <span>{item.source_platform || '来源未知'}</span>
                      </p>
                      <p className="intake-item-meta intake-alignment">{alignmentText(item)}</p>
                      {item.blockers && item.blockers.length > 0 && (
                        <p className="intake-blockers">
                          待办：{item.blockers.map(blockerText).join('；')}
                        </p>
                      )}
                    </div>
                    <div className="intake-item-actions">
                      <button
                        className="secondary"
                        type="button"
                        aria-expanded={expandedTranscript === item.item_id}
                        aria-controls={expandedTranscript === item.item_id ? `transcript-${safeId}` : undefined}
                        onClick={() => toggleTranscript(item)}
                        disabled={Boolean(busy) || item.ingested}
                        title={item.ingested ? '已入库素材不在此处改稿' : undefined}
                      >
                        写/改文字稿
                      </button>
                      <button
                        className="secondary"
                        type="button"
                        onClick={() => alignItem(item)}
                        disabled={Boolean(busy) || item.ingested || !item.has_transcript}
                        title={item.ingested
                          ? '已入库素材不在此处改稿'
                          : (item.has_transcript ? undefined : '请先保存文字稿')}
                      >
                        {busy === `align:${item.item_id}` ? '对齐中…' : '对齐'}
                      </button>
                      <button
                        className="secondary"
                        type="button"
                        aria-expanded={approvalOpen === item.item_id}
                        aria-controls={approvalOpen === item.item_id ? `approval-${safeId}` : undefined}
                        onClick={() => toggleApproval(item)}
                        disabled={item.ingested || Boolean(busy)}
                      >
                        {item.ingested ? '已入库' : '审核'}
                      </button>
                      <button
                        className="primary"
                        type="button"
                        onClick={() => startTraining(item)}
                        disabled={!item.can_train || Boolean(busy)}
                        title={item.can_train
                          ? undefined
                          : (item.ingested
                            ? '已入库，但尚未进入当前档位可选池'
                            : '需审核入库并进入当前档位可选池后才能开始训练')}
                      >
                        开始训练
                      </button>
                    </div>
                  </div>

                  {item.material_present ? (
                    <div className="intake-audio">
                      <audio controls preload="metadata" src={`/api/materials/${encodeURIComponent(item.material_id)}/audio`} />
                      <span className="intake-hint">来自素材库的音频；可边听边校对。</span>
                    </div>
                  ) : (
                    <p className="intake-hint intake-no-audio">素材尚未入库，入库后才可试听；当前只展示暂存文件信息。</p>
                  )}

                  {expandedTranscript === item.item_id && (
                    <div className="intake-editor" id={`transcript-${safeId}`}>
                      {item.has_transcript && (
                        <p className="intake-hint">
                          已载入当前文字稿（{item.transcript_chars} 字符）。修改后保存会覆盖旧稿，
                          并使旧对齐与审核证据失效。
                        </p>
                      )}
                      <label className="intake-field" htmlFor={`draft-${safeId}`}>
                        文字稿（每行一句，至少 3 行）
                        <textarea
                          id={`draft-${safeId}`}
                          rows={8}
                          value={draft}
                          onChange={(event) => setDrafts((current) => ({ ...current, [item.item_id]: event.target.value }))}
                          placeholder="每行输入一句，作为对齐与听写的句子边界"
                          disabled={busy === `transcript:${item.item_id}`}
                        />
                      </label>
                      <div className="intake-editor-footer">
                        <span className="intake-hint">{lineCount} 行 · {draft.length} 字符</span>
                        <div className="intake-editor-actions">
                          <button className="secondary" type="button" onClick={() => setExpandedTranscript('')}>取消</button>
                          <button
                            className="primary"
                            type="button"
                            onClick={() => saveTranscript(item)}
                            disabled={!draft.trim() || Boolean(busy)}
                          >
                            {busy === `transcript:${item.item_id}` ? '保存中…' : '保存文字稿'}
                          </button>
                        </div>
                      </div>
                    </div>
                  )}

                  {approvalOpen === item.item_id && (
                    <form className="intake-editor" id={`approval-${safeId}`} onSubmit={(event) => submitApproval(event, item)}>
                      <div className="intake-approval-line">
                        <span>口音</span>
                        <strong>en-GB（固定，不接受其它值）</strong>
                      </div>
                      <label className="intake-field">
                        许可状态
                        <select
                          required
                          value={approval.license}
                          onChange={(event) => updateApproval(item.item_id, { license: event.target.value })}
                        >
                          <option value="">请选择（仅 CLEARED 可入库）</option>
                          <option value="CLEARED">CLEARED</option>
                        </select>
                      </label>
                      <label className="intake-check">
                        <input
                          type="checkbox"
                          checked={approval.accentConfirmed}
                          onChange={(event) => updateApproval(item.item_id, { accentConfirmed: event.target.checked })}
                        />
                        我确认口音为英式（en-GB）
                      </label>
                      <label className="intake-check">
                        <input
                          type="checkbox"
                          checked={approval.transcriptVerified}
                          onChange={(event) => updateApproval(item.item_id, { transcriptVerified: event.target.checked })}
                        />
                        我已逐字校对文字稿
                      </label>
                      <label className="intake-check">
                        <input
                          type="checkbox"
                          checked={approval.qualityOk}
                          onChange={(event) => updateApproval(item.item_id, { qualityOk: event.target.checked })}
                        />
                        音质合格
                      </label>
                      <label className="intake-check">
                        <input
                          type="checkbox"
                          checked={approval.confirmed}
                          onChange={(event) => updateApproval(item.item_id, { confirmed: event.target.checked })}
                        />
                        我确认以上信息真实，并提交审核
                      </label>
                      {item.blockers && item.blockers.length > 0 && (
                        <p className="intake-hint">当前仍不可提交：{item.blockers.map(blockerText).join('；')}。</p>
                      )}
                      <div className="intake-editor-footer">
                        <span className="intake-hint">审核通过后写入正式素材库；是否进入当前档位可选由素材池决定。</span>
                        <div className="intake-editor-actions">
                          <button className="secondary" type="button" onClick={() => setApprovalOpen('')}>取消</button>
                          <button
                            className="primary"
                            type="submit"
                            disabled={!canSubmitApproval}
                            title={canSubmitApproval
                              ? undefined
                              : (item.can_approve ? '请完成许可选择与全部确认项' : '后端证据不足：请先补齐文字稿与可用对齐')}
                          >
                            {busy === `approve:${item.item_id}` ? '提交中…' : '提交审核'}
                          </button>
                        </div>
                      </div>
                    </form>
                  )}

                  {preview && preview.length > 0 && (
                    <details className="intake-align-preview">
                      <summary>查看对齐预览（前 {preview.length} 行）</summary>
                      <ol>
                        {preview.map((line, index) => (
                          <li key={`${item.item_id}-${index}`}>
                            <span className="intake-time">{formatClock(line.start)}–{formatClock(line.end)}</span>
                            <span>{line.text}</span>
                            {line.repeats > 1 && <span className="intake-hint">（重复 {line.repeats} 遍）</span>}
                          </li>
                        ))}
                      </ol>
                    </details>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}

export default MaterialImportPanel
