import { useRef, useState } from 'react'
import { startWavRecording } from './wavRecorder.js'

const LEVEL_LABELS = { PASS: '达标', CLOSE: '接近', FAIL: '未达标' }

function levelClass(level) {
  return { PASS: 'lv-pass', CLOSE: 'lv-close', FAIL: 'lv-fail' }[level] || 'lv-fail'
}

function blobToBase64(blob) {
  return blob.arrayBuffer().then((arrayBuffer) => {
    const bytes = new Uint8Array(arrayBuffer)
    let binary = ''
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000))
    }
    return btoa(binary)
  })
}

function ReadingPanel({ materialId, scope, partNo, sentences, onPartComplete, onMessage, onScored }) {
  const [recording, setRecording] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const recorderRef = useRef(null)
  const audioRef = useRef(null)

  const title = scope === 'FULL' ? '全文朗读验收' : `朗读 Part ${partNo}`
  const text = sentences.map((sentence) => sentence.text).join(' ')

  const startRecording = () => {
    startWavRecording()
      .then((recorder) => {
        recorderRef.current = recorder
        setResult(null)
        setRecording(true)
        onMessage('正在录音：看着原文，以接近原音的速度、停顿和重音完整朗读。')
      })
      .catch(() => onMessage('无法访问麦克风，请检查浏览器权限后重试。'))
  }

  const stopRecording = () => {
    setRecording(false)
    setBusy(true)
    recorderRef.current
      .stop()
      .then(blobToBase64)
      .then((contentBase64) => {
        const url = scope === 'FULL'
          ? `/api/materials/${materialId}/full-reading/score`
          : `/api/materials/${materialId}/reading-parts/${partNo}/score`
        return fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: `${materialId}-${scope}${partNo || ''}-${Date.now()}.wav`,
            content_base64: contentBase64,
          }),
        })
      })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '评分失败')
        return payload
      })
      .then((payload) => {
        setResult(payload)
        setBusy(false)
        onMessage(payload.overall_pass ? '三维全部达标。' : '本次未全部达标，可再练。')
        if (onScored) onScored(payload)
      })
      .catch((error) => {
        onMessage(error.message)
        setBusy(false)
      })
  }

  const complete = () => {
    const url = scope === 'FULL'
      ? `/api/materials/${materialId}/full-reading-assessment`
      : `/api/materials/${materialId}/reading-parts/${partNo}/complete`
    const body = scope === 'FULL' ? { passed: true } : undefined
    fetch(url, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '推进失败')
        return payload
      })
      .then(onPartComplete)
      .catch((error) => onMessage(error.message))
  }

  return (
    <div className="reading-panel">
      <p className="reading-title">{title}</p>
      <div className="reading-text">{text}</div>
      <div className="reading-controls">
        <audio ref={audioRef} src={`/api/materials/${materialId}/audio`} preload="metadata" controls />
        <div className="dictation-actions">
          {!recording ? (
            <button className="primary" onClick={startRecording} disabled={busy}>● 开始录音跟读</button>
          ) : (
            <button className="danger" onClick={stopRecording} disabled={busy}>■ 停止并评分</button>
          )}
          {busy && <span className="listen-count">正在分析录音…</span>}
        </div>
      </div>

      {result && (
        <div className="reading-result">
          <p className="feedback-title">段尾反馈（语速 / 停顿 / 重音分别判定）</p>
          <div className="dimension-blocks">
            <div className={`dimension-block ${levelClass(result.speed)}`}>
              <strong>语速</strong>
              <span>{LEVEL_LABELS[result.speed]}</span>
              <small>参考 {result.reference_duration.toFixed(1)}s · 你的 {result.user_duration.toFixed(1)}s</small>
            </div>
            <div className={`dimension-block ${levelClass(result.pause)}`}>
              <strong>停顿</strong>
              <span>{LEVEL_LABELS[result.pause]}</span>
              <small>参考 {result.reference_pause_count} 处 · 你的 {result.user_pause_count} 处</small>
            </div>
            <div className={`dimension-block ${levelClass(result.stress)}`}>
              <strong>重音</strong>
              <span>{LEVEL_LABELS[result.stress]}</span>
              <small>能量起伏结构对比（简化代理，待校准）</small>
            </div>
          </div>
          {result.overall_pass ? (
            <button className="primary" onClick={complete}>三维达标，完成{scope === 'FULL' ? '全文验收' : ` Part ${partNo}`}</button>
          ) : (
            <p className="muted">未全部达标：再听原音，注意对应维度后重录。</p>
          )}
        </div>
      )}
    </div>
  )
}

export default ReadingPanel
