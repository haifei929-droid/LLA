import { useRef, useState } from 'react'

// Hint formats are a Spec "待校准" item; these minimal placeholders only
// reveal position and length, never the word itself.
const HINT_LABELS = {
  0: '需要提示',
  1: '提示：注意第 N 个词附近',
  2: '再提示：该词共 X 个字母',
}

const ERROR_LABELS = {
  MISS: '漏听',
  MISHEARD: '听错',
  WORD_FORM: '词形',
  SPELLING: '拼写',
  ACTIVE_BLANK: '主动留空',
}

function errorClass(errorType) {
  return {
    MISS: 'err-miss',
    MISHEARD: 'err-misheard',
    WORD_FORM: 'err-wordform',
    SPELLING: 'err-spelling',
    ACTIVE_BLANK: 'err-blank',
  }[errorType] || 'err-blank'
}

function newOperationId() {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function DictationPanel({ materialId, context, onTransition, onPartComplete, onMessage }) {
  const [input, setInput] = useState('')
  const [listenCount, setListenCount] = useState(0)
  const [result, setResult] = useState(null)
  const [hintLevel, setHintLevel] = useState(0)
  const [revealedText, setRevealedText] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [partSubmitting, setPartSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const partSubmittingRef = useRef(false)
  const audioRef = useRef(null)

  const { sentences, part_no: partNo } = context
  const current = sentences.find((sentence) => !sentence.is_exact)
  const doneCount = sentences.filter((sentence) => sentence.is_exact).length

  // Defensive/recovery path only: the normal flow completes a Part atomically
  // inside sentence submit, so this branch is only reached for legacy data
  // where a Part is fully exact but the state has not advanced yet.
  const completePart = async () => {
    if (partSubmittingRef.current) return
    partSubmittingRef.current = true
    setPartSubmitting(true)
    try {
      await onPartComplete()
    } catch {
      // 失败信息已由 onPartComplete 内层 setMessage 展示；这里仅恢复按钮可重试
    } finally {
      partSubmittingRef.current = false
      setPartSubmitting(false)
    }
  }

  if (!current) {
    return (
      <div className="dictation-panel">
        <p className="notice">Part {partNo} 的所有句子都已逐字正确。</p>
        <button className="primary" onClick={completePart} disabled={partSubmitting}>
          完成 Part {partNo}，进入下一步
        </button>
      </div>
    )
  }

  const playSentence = () => {
    const audio = audioRef.current
    if (!audio) return
    audio.currentTime = current.start_time
    audio.play()
    setListenCount((count) => count + 1)
  }

  const stopAtEnd = () => {
    const audio = audioRef.current
    if (audio && current && audio.currentTime >= current.end_time) {
      audio.pause()
    }
  }

  const submit = (revealed) => {
    if (submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    fetch(`/api/materials/${materialId}/sentences/${current.sentence_id}/dictation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_text: input,
        listen_count: listenCount,
        hint_level: revealed ? 0 : hintLevel,
        revealed,
        operation_id: newOperationId(),
      }),
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '提交失败')
        return payload
      })
      .then((payload) => {
        if (payload.is_exact_match) {
          // The server is the sole transition authority: it returns the next
          // state / sentence / action, and (for continue-dictation) the next
          // context to render directly. The frontend derives nothing locally.
          onMessage('本句逐字正确。')
          setInput('')
          setListenCount(0)
          setResult(null)
          setHintLevel(0)
          setRevealedText(null)
          onTransition(payload)
        } else {
          setResult(payload)
          if (payload.revealed || payload.expected_text) {
            setRevealedText(payload.expected_text)
          }
        }
      })
      .catch((error) => onMessage(error.message))
      .finally(() => {
        submittingRef.current = false
        setSubmitting(false)
      })
  }

  const giveHint = () => {
    const nextLevel = hintLevel + 1
    setHintLevel(nextLevel)
    const firstError = result && result.errors[0]
    if (nextLevel === 1) {
      onMessage(`提示：注意第 ${(firstError?.expected_index ?? 0) + 1} 个词附近，再听一遍。`)
    } else if (nextLevel === 2 && firstError?.expected) {
      onMessage(`再提示：该词共 ${firstError.expected.length} 个字母，以 ${firstError.expected[0].toUpperCase()} 开头。`)
    }
  }

  const skipAfterReveal = () => {
    // Reveal never lowers the pass bar (Spec 6.3): the sentence still has to
    // be dictated exactly. Reset the local state so the user re-submits with
    // the transcript visible.
    setInput('')
    setListenCount(0)
    setResult(null)
    setHintLevel(0)
    setRevealedText(null)
  }

  const insertBlank = () => {
    setInput((text) => `${text} ____ `.trim())
  }

  return (
    <div className="dictation-panel">
      <audio ref={audioRef} src={`/api/materials/${materialId}/audio`} preload="metadata" onTimeUpdate={stopAtEnd} />
      <div className="dictation-progress">
        <span>Part {partNo}</span>
        <span>第 {sentences.indexOf(current) + 1} / {sentences.length} 句</span>
        <span>已正确 {doneCount} / {sentences.length}</span>
      </div>

      <div className="dictation-controls">
        <button className="secondary" onClick={playSentence}>▶ 播放本句（第 {listenCount + 1} 遍）</button>
        <span className="listen-count">已听 {listenCount} 遍{listenCount < 1 ? ' · 先听一遍再开始写' : ''}</span>
      </div>

      <textarea
        className="dictation-input"
        rows={2}
        value={input}
        onChange={(event) => setInput(event.target.value)}
        placeholder="写下你听到的内容；没听出来的词用 ____ 代替"
        disabled={listenCount < 1 && !revealedText}
      />
      <div className="dictation-actions">
        <button className="secondary" onClick={insertBlank}>这里没听出来 ____</button>
        <button className="primary" onClick={() => submit(false)} disabled={listenCount < 1 || submitting}>提交</button>
      </div>

      {result && !result.is_exact_match && !revealedText && (
        <div className="dictation-feedback">
          <p className="feedback-title">本次听写有 {result.errors.length} 处需要修正（不显示答案）</p>
          <div className="error-blocks">
            {result.errors.map((error, index) => (
              <span key={index} className={`error-block ${errorClass(error.error_type)}`}>
                <strong>{ERROR_LABELS[error.error_type] || error.error_type}</strong>
                <small>第 {(error.expected_index ?? 0) + 1} 词</small>
              </span>
            ))}
          </div>
          <div className="dictation-actions">
            <button className="secondary" onClick={giveHint} disabled={hintLevel >= 2}>
              {HINT_LABELS[hintLevel + 1] || '再听一遍'}
            </button>
            <button className="danger" onClick={() => submit(true)} disabled={submitting}>Reveal 原文</button>
          </div>
        </div>
      )}

      {revealedText && (
        <div className="reveal-box">
          <p className="feedback-title">原文（已记录为 Reveal，不计入听出遍数）。请照原文再听写一遍，本句仍需逐字正确才能通过。</p>
          <blockquote>{revealedText}</blockquote>
          <div className="dictation-actions">
            <button className="secondary" onClick={skipAfterReveal}>清空重写</button>
            <button className="primary" onClick={() => submit(false)} disabled={listenCount < 1 || submitting}>提交本句</button>
          </div>
        </div>
      )}
    </div>
  )
}

export default DictationPanel
