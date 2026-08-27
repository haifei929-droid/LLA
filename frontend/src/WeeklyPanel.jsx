import { useEffect, useRef, useState } from 'react'
import ReadingPanel from './ReadingPanel.jsx'

const GATE_LABELS = {
  WEEKLY_ASSESSMENT_READY: '待测试',
  REINFORCEMENT_REQUIRED: '未通过，需要强化',
  REINFORCEMENT: '强化训练中',
  WEEKLY_GATE_PASS: '已通过',
}

function materialIdOf(sentenceId) {
  // sentence ids look like "m1-sentence-001"; the audio lives on the material.
  return sentenceId ? sentenceId.split('-sentence-')[0] : null
}

function ItemDictation({ weekId, item, index, kind, onMessage, onDone }) {
  const [input, setInput] = useState('')
  const [result, setResult] = useState(null)
  const audioRef = useRef(null)
  const materialId = materialIdOf(item.sentence_id)

  const play = () => {
    const audio = audioRef.current
    if (!audio) return
    if (item.start_time != null) audio.currentTime = item.start_time
    audio.play()
  }

  const submit = () => {
    const url = kind === 'REINFORCEMENT'
      ? `/api/weekly-assessments/${weekId}/reinforcement/items/${item.item_id}/dictation`
      : `/api/weekly-assessments/${weekId}/test-items/${item.item_id}/dictation`
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_text: input, listen_count: 1 }),
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '提交失败')
        return payload
      })
      .then((payload) => {
        setResult(payload)
        if (payload.is_exact_match) {
          onMessage(`第 ${index + 1} 句逐字正确。`)
          onDone(payload)
        } else {
          onMessage('未完全正确，再听一遍后修改。')
        }
      })
      .catch((error) => onMessage(error.message))
  }

  if (item.is_exact) {
    return <div className="weekly-item done"><span>{index + 1}.</span><span>✅ 已正确</span></div>
  }

  return (
    <div className="weekly-item">
      <span>{index + 1}.</span>
      {materialId && <audio ref={audioRef} src={`/api/materials/${materialId}/audio`} preload="metadata" />}
      <button className="secondary" onClick={play}>▶ 播放</button>
      <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="写下听到的内容，____ 表示没听出来" />
      <button className="primary" onClick={submit}>提交</button>
      {result && !result.is_exact_match && <span className="muted">错误 {result.errors.length} 处</span>}
    </div>
  )
}

function WeeklyPanel({ onMessage }) {
  const [assessments, setAssessments] = useState([])
  const [current, setCurrent] = useState(null)
  const [testItems, setTestItems] = useState(null)
  const [readingMaterial, setReadingMaterial] = useState(null)

  const refresh = () => fetch('/api/weekly-assessments')
    .then((response) => response.json())
    .then(setAssessments)
    .catch(() => onMessage('无法加载周测状态。'))

  useEffect(() => { refresh() }, [])

  const open = (assessment) => {
    setCurrent(assessment)
    setReadingMaterial(null)
    setTestItems(null)
    fetch(`/api/weekly-assessments/${assessment.week_id}/test-items?kind=TEST`)
      .then((response) => response.json())
      .then(setTestItems)
      .catch(() => setTestItems([]))
  }

  const create = () => {
    const now = new Date()
    const week = `week-${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`
    fetch('/api/weekly-assessments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ week_id: week, period_start: '', period_end: '' }),
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '创建周测失败')
        return payload
      })
      .then((assessment) => { open(assessment); refresh(); onMessage('本周周测已创建。') })
      .catch((error) => onMessage(error.message))
  }

  const refreshAssessment = () => fetch(`/api/weekly-assessments/${current.week_id}`)
    .then((response) => response.json())
    .then((assessment) => { setCurrent(assessment); refresh(); return assessment })
    .catch(() => null)

  const generateTest = () => {
    fetch(`/api/weekly-assessments/${current.week_id}/test-items`, { method: 'POST' })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '生成测试失败')
        return payload
      })
      .then((items) => { setTestItems(items); onMessage(`听写测试已生成（${items.length} 句）。`) })
      .catch((error) => onMessage(error.message))
  }

  const startReinforcement = () => {
    fetch(`/api/weekly-assessments/${current.week_id}/reinforcement/start`, { method: 'POST' })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '生成强化包失败')
        return payload
      })
      .then((assessment) => {
        setCurrent(assessment)
        setTestItems(assessment.reinforcement_items || [])
        onMessage(`强化包已生成（${(assessment.reinforcement_items || []).length} 句）。`)
      })
      .catch((error) => onMessage(error.message))
  }

  const confirmRetest = () => {
    fetch(`/api/weekly-assessments/${current.week_id}/retest/confirm`, { method: 'POST' })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '确认复测失败')
        return payload
      })
      .then((assessment) => {
        setCurrent(assessment)
        onMessage('针对性复测通过，本周 Gate 已恢复，可推荐进入下一轮。')
        refresh()
      })
      .catch((error) => onMessage(error.message))
  }

  const openReadingTest = () => {
    // The reading weekly test reuses a material the user actually trained to
    // the reading stage this week (that is exactly when reading_required is
    // inferred); its reference audio and sentences feed the same scorer.
    fetch('/api/materials')
      .then((response) => response.json())
      .then((materials) => materials.find((material) =>
        material.current_state === 'READING_AVAILABLE' || material.current_state === 'FULL_READING_ASSESSMENT'))
      .then((material) => {
        if (!material) throw new Error('没有已解锁朗读的素材，无法进行朗读周测')
        return fetch(`/api/materials/${material.material_id}`).then((response) => response.json())
      })
      .then(setReadingMaterial)
      .catch((error) => onMessage(error.message))
  }

  const onReadingScored = (result) => {
    const dimensions = {
      speed: result.speed === 'PASS',
      pause: result.pause === 'PASS',
      stress: result.stress === 'PASS',
    }
    fetch(`/api/weekly-assessments/${current.week_id}/reading`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dimensions }),
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '记录朗读测试失败')
        return payload
      })
      .then((assessment) => {
        setCurrent(assessment)
        setReadingMaterial(null)
        onMessage('朗读测试已记录。')
      })
      .catch((error) => onMessage(error.message))
  }

  if (!current) {
    return (
      <div className="weekly-panel">
        <p className="reading-title">周测 Gate（决定是否推荐进入下一轮，不硬锁）</p>
        {assessments.length ? (
          <div className="weekly-history">
            {assessments.map((assessment) => (
              <button key={assessment.week_id} className="material-row dark-row" onClick={() => open(assessment)}>
                <div><strong>{assessment.week_id}</strong><p>{GATE_LABELS[assessment.gate_status] || assessment.gate_status} · 听写 {assessment.dictation_score ?? '—'}%</p></div>
                <span>{assessment.gate_status}</span>
              </button>
            ))}
          </div>
        ) : <p className="muted">本周还没有周测。</p>}
        <button className="primary" onClick={create}>开始本周周测</button>
      </div>
    )
  }

  const gatePassed = current.gate_status === 'WEEKLY_GATE_PASS'
  const inReinforcement = current.gate_status === 'REINFORCEMENT_REQUIRED' || current.gate_status === 'REINFORCEMENT' || current.gate_status === 'TARGETED_RETEST'

  if (readingMaterial) {
    const partNo = [1, 2, 3].find((part) => !(readingMaterial.reading_part_status || {})[String(part)]) ?? 1
    return <div className="weekly-panel">
      <button className="back" onClick={() => setReadingMaterial(null)}>← 返回周测</button>
      <ReadingPanel
        materialId={readingMaterial.material_id}
        scope="PART"
        partNo={partNo}
        sentences={readingMaterial.sentences.filter((sentence) => sentence.part_no === partNo)}
        onPartComplete={() => {}}
        onScored={onReadingScored}
        onMessage={onMessage}
      />
    </div>
  }

  const itemKind = current.gate_status === 'REINFORCEMENT' ? 'REINFORCEMENT' : 'TEST'

  return (
    <div className="weekly-panel">
      <p className="reading-title">{current.week_id} · {GATE_LABELS[current.gate_status] || current.gate_status}</p>
      <div className="weekly-meta">
        <span>听写测试：{current.dictation_required ? (current.dictation_score != null ? `${current.dictation_score}%` : '待完成') : '不需要'}</span>
        <span>朗读测试：{current.reading_required ? '需要' : '不需要'}</span>
        <span>分数 &gt;= 80% 才算听写通过</span>
      </div>

      {current.gate_status === 'WEEKLY_ASSESSMENT_READY' && current.dictation_required && (!testItems || testItems.length === 0) && (
        <button className="primary" onClick={generateTest}>生成听写测试</button>
      )}

      {testItems && testItems.length > 0 && itemKind === 'TEST' && (
        <div className="weekly-items">
          <p className="feedback-title">听写测试（逐字正确才算通过，全部完成后自动评分）</p>
          {testItems.map((item, index) => (
            <ItemDictation
              key={item.item_id}
              weekId={current.week_id}
              item={item}
              index={index}
              kind="TEST"
              onMessage={onMessage}
              onDone={() => {
                refreshAssessment()
                fetch(`/api/weekly-assessments/${current.week_id}/test-items?kind=TEST`)
                  .then((response) => response.json())
                  .then(setTestItems)
              }}
            />
          ))}
        </div>
      )}

      {inReinforcement && (
        <div className="weekly-items">
          <p className="feedback-title">强化训练包（只覆盖失败能力，全部正确后进入针对性复测）</p>
          {current.gate_status === 'TARGETED_RETEST' ? (
            <div className="weekly-item done">
              <span>✅ 强化项全部正确</span>
              <button className="primary" onClick={confirmRetest}>确认复测通过，恢复推荐</button>
            </div>
          ) : testItems && testItems.length > 0 && itemKind === 'REINFORCEMENT' ? (
            testItems.map((item, index) => (
              <ItemDictation
                key={item.item_id}
                weekId={current.week_id}
                item={item}
                index={index}
                kind="REINFORCEMENT"
                onMessage={onMessage}
                onDone={(payload) => {
                  if (payload.assessment) setCurrent(payload.assessment)
                  if (payload.assessment && payload.assessment.gate_status !== 'TARGETED_RETEST') {
                    startReinforcement()
                  }
                }}
              />
            ))
          ) : (
            <button className="primary" onClick={startReinforcement}>开始强化训练</button>
          )}
        </div>
      )}

      {current.reading_required && !gatePassed && !readingMaterial && (
        <button className="secondary" onClick={openReadingTest}>朗读测试（读一段当周素材）</button>
      )}

      {gatePassed && <p className="notice">✅ 本周 Gate 已通过，可以推荐进入下一轮。</p>}
      <button className="back" onClick={() => { setCurrent(null); refresh() }}>← 返回周测列表</button>
    </div>
  )
}

export default WeeklyPanel
