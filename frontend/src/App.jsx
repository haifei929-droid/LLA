import { useEffect, useState } from 'react'
import DictationPanel from './DictationPanel.jsx'
import ReadingPanel from './ReadingPanel.jsx'
import WeeklyPanel from './WeeklyPanel.jsx'
import CandidatePanel from './CandidatePanel.jsx'
import P2Dashboard from './P2Dashboard.jsx'

const blankMaterial = { material_id: '', title: '', audio_path: '', transcript: '' }

function progressRank(state) {
  // Materials still being trained rank before completed ones.
  if (!state || state === 'FULLY_COMPLETED' || state === 'LISTENING_COMPLETED') return 1
  return 0
}

function App() {
  const [health, setHealth] = useState('连接检查中…')
  const [materials, setMaterials] = useState([])
  const [stats, setStats] = useState(null)
  const [view, setView] = useState('home')
  const [candidateStage, setCandidateStage] = useState('STAGE_1')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const [showImport, setShowImport] = useState(false)
  const [form, setForm] = useState(blankMaterial)
  const [message, setMessage] = useState('')
  const [comprehension, setComprehension] = useState({ rating: '30–50%', summary: '' })
  const [dictationContext, setDictationContext] = useState(null)
  const [firstListenPlayed, setFirstListenPlayed] = useState(false)

  const refresh = () => Promise.all([
    fetch('/api/materials').then((response) => response.json()),
    fetch('/api/stats').then((response) => response.json()),
  ]).then(([materialPayload, statsPayload]) => {
    setMaterials(materialPayload)
    setStats(statsPayload)
  })

  useEffect(() => {
    Promise.all([
      fetch('/api/health').then((response) => response.json()),
    ])
      .then(([healthPayload]) => {
        setHealth(healthPayload.status === 'ok' ? '训练核心已就绪' : '训练核心异常')
        return refresh()
      })
      .catch(() => setHealth('后端尚未启动'))
  }, [])

  const totalMinutes = stats ? Math.floor(stats.total_learning_seconds / 60) : 0

  const search = (event) => {
    event.preventDefault()
    fetch(`/api/materials/search?q=${encodeURIComponent(query)}`)
      .then((response) => response.json())
      .then(setMaterials)
      .catch(() => setMessage('素材搜索失败，请确认后端已启动。'))
  }

  const openMaterial = (materialId) => {
    fetch(`/api/materials/${encodeURIComponent(materialId)}`)
      .then((response) => {
        if (!response.ok) throw new Error('material')
        return response.json()
      })
      .then((payload) => {
        setSelected(payload)
        setDictationContext(null)
        setFirstListenPlayed(false)
        setView('training')
        setMessage('')
      })
      .catch(() => setMessage('无法打开素材详情。'))
  }

  const refreshDictationContext = () => {
    if (!selected || !selected.current_state?.startsWith('DICTATION_PART_')) return
    fetch(`/api/materials/${selected.material_id}/dictation-context`)
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '无法加载听写上下文')
        return payload
      })
      .then(setDictationContext)
      .catch((error) => setMessage(error.message))
  }

  useEffect(() => {
    refreshDictationContext()
  }, [selected && selected.current_state])

  const postTrainingEvent = (path, body) => {
    fetch(path, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '操作失败')
        return payload
      })
      .then((progress) => {
        setSelected((current) => current ? { ...current, ...progress } : current)
        refresh()
        setMessage('状态已更新。')
      })
      .catch((error) => setMessage(error.message))
  }

  const createMaterial = (event) => {
    event.preventDefault()
    const lines = form.transcript.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
    if (lines.length < 3) {
      setMessage('至少输入 3 行句子，系统才能分成三个 Part。')
      return
    }
    fetch('/api/materials', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...form,
        transcript: lines.join(' '),
        timestamped_sentences: lines.map((text, index) => ({
          text, start_time: index * 8, end_time: (index + 1) * 8,
        })),
      }),
    })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '素材导入失败')
        return payload
      })
      .then((payload) => {
        setForm(blankMaterial)
        setShowImport(false)
        setMessage('素材已导入，可以进入训练。')
        refresh().then(() => openMaterial(payload.material_id))
      })
      .catch((error) => setMessage(error.message))
  }

  const trainingBody = () => {
    if (!selected) return null
    const state = selected.current_state
    if (state === 'READY_FIRST_LISTEN') {
      return <div className="event-panel">
        <p>第一次完整盲听：完整播放整段素材，不显示字幕、原文或关键词。可以暂停，但请真实播放到结束。</p>
        <p className="muted">{firstListenPlayed ? '✅ 已完整播放到结尾，可以提交。' : '请在上方音频面板播放整段素材直到结束（进度条到末尾）。'}</p>
        <button className="primary" onClick={() => postTrainingEvent(`/api/materials/${selected.material_id}/first-listen/complete`)} disabled={!firstListenPlayed}>已完成首次盲听</button>
      </div>
    }
    if (state === 'FIRST_COMPREHENSION_CHECK' || state === 'SECOND_COMPREHENSION_CHECK') {
      const phase = state.startsWith('FIRST') ? 'FIRST' : 'SECOND'
      return <form className="event-form" onSubmit={(event) => {
        event.preventDefault()
        postTrainingEvent(`/api/materials/${selected.material_id}/comprehension-check`, {
          phase, self_rating: comprehension.rating, summary: comprehension.summary,
        })
      }}>
        <label>理解自评<select value={comprehension.rating} onChange={(event) => setComprehension({ ...comprehension, rating: event.target.value })}><option>&lt;30%</option><option>30–50%</option><option>50–70%</option><option>&gt;70%</option></select></label>
        <label>一句话总结<textarea required value={comprehension.summary} onChange={(event) => setComprehension({ ...comprehension, summary: event.target.value })} /></label>
        <button className="primary" type="submit">提交理解检查</button>
      </form>
    }
    if (state.startsWith('DICTATION_PART_')) {
      if (!dictationContext) return <p className="muted">正在加载听写上下文…</p>
      return <DictationPanel
        materialId={selected.material_id}
        context={dictationContext}
        onPartComplete={() => {
          const part = Number(state.slice(-1))
          postTrainingEvent(`/api/materials/${selected.material_id}/dictation-parts/${part}/complete`)
          setDictationContext(null)
        }}
        onMessage={setMessage}
      />
    }
    if (state === 'SECOND_FULL_LISTEN') return <div className="event-panel">
      <p>第二次完整听：三个 Part 听写已全部完成。现在可以查看原文，完整听一遍全文。</p>
      <blockquote className="transcript">{selected.transcript}</blockquote>
      <button className="primary" onClick={() => postTrainingEvent(`/api/materials/${selected.material_id}/second-listen/complete`)}>已完成二次复听</button>
    </div>
    if (state === 'READING_AVAILABLE' || state === 'FULL_READING_ASSESSMENT') {
      if (state === 'READING_AVAILABLE') {
        const status = selected.reading_part_status || {}
        const partNo = [1, 2, 3].find((part) => !status[String(part)]) ?? 1
        return <ReadingPanel
          materialId={selected.material_id}
          scope="PART"
          partNo={partNo}
          sentences={selected.sentences.filter((sentence) => sentence.part_no === partNo)}
          onPartComplete={(progress) => { setSelected({ ...selected, ...progress }); setMessage('朗读 Part 完成。') }}
          onMessage={setMessage}
        />
      }
      return <ReadingPanel
        materialId={selected.material_id}
        scope="FULL"
        partNo={null}
        sentences={selected.sentences}
        onPartComplete={(progress) => { setSelected({ ...selected, ...progress }); setMessage('全文朗读验收通过，素材已完成。') }}
        onMessage={setMessage}
      />
    }
    if (state === 'LISTENING_COMPLETED' || state === 'FULLY_COMPLETED') {
      return <div className="event-panel">
        <p>本篇素材已完成。按学习节奏自动搜索下一篇（难度规则：一次只升级一个变量，周测稳定后由 Agent 决定是否升级）。</p>
        <button className="primary" onClick={searchNext} disabled={searching}>{searching ? '正在搜索素材并转录时间戳（约 1-2 分钟）…' : '获取下一篇素材'}</button>
      </div>
    }
    return <p>当前状态：{state}</p>
  }

  const [searching, setSearching] = useState(false)
  const searchNext = () => {
    setSearching(true)
    fetch('/api/materials/next', { method: 'POST' })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '搜索下一篇失败')
        return payload
      })
      .then((payload) => {
        setSearching(false)
        onMaterialImported(payload.material_id)
        setMessage(payload.upgrade_available
          ? `已获取下一篇（周测稳定，难度档已升级）。来源：${payload.source_name}`
          : `已获取下一篇。来源：${payload.source_name}`)
      })
      .catch((error) => { setSearching(false); setMessage(error.message) })
  }

  const onMaterialImported = (materialId) => {
    fetch(`/api/materials/${encodeURIComponent(materialId)}`)
      .then((response) => response.json())
      .then((payload) => {
        setSelected(payload)
        setDictationContext(null)
        refresh()
      })
      .catch(() => setMessage('素材已导入，刷新列表后可见。'))
  }

  const skipMaterial = () => {
    if (!selected) return
    setMessage('正在跳过当前素材并搜索替代素材…')
    fetch(`/api/materials/${selected.material_id}/skip`, { method: 'POST' })
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '跳过失败')
        return payload
      })
      .then(() => { refresh(); searchNext() })
      .catch((error) => setMessage('跳过失败：' + error.message))
  }

  const progressMatrix = () => {
    if (!selected) return null
    const dictationStatus = selected.dictation_part_status || {}
    const readingStatus = selected.reading_part_status || {}
    return (
      <div className="progress-matrix">
        {[1, 2, 3].map((part) => (
          <div className="matrix-cell" key={part}>
            <strong>Part {part}</strong>
            <span className={dictationStatus[String(part)] ? 'dot done' : 'dot todo'}>听写</span>
            <span className={readingStatus[String(part)] ? 'dot done' : 'dot locked'}>朗读</span>
          </div>
        ))}
      </div>
    )
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <div>
          <p className="eyebrow">LANGUAGE TRAINING AGENT · P0</p>
          <h1>Training Workspace</h1>
        </div>
        <span className="status">{health}</span>
      </header>

      {view === 'home' && <>
      <section className="hero">
        <p className="eyebrow">当前阶段</p>
        <h2>把每一次练习，变成可恢复的进步</h2>
        <p>训练状态、素材进度和学习时长由 Training Core 与 SQLite 保存，下一次打开仍能从上次的位置继续。</p>
      </section>

      <section className="grid">
        <button className="card metric action-card" onClick={() => setView('materials')}><span>声音训练素材</span><strong>{materials.length}</strong><p>点击进入素材搜索与训练</p></button>
        <article className="card metric"><span>累计学习</span><strong>{totalMinutes}<small> 分钟</small></strong><p>由活动日志自动汇总</p></article>
        <button className="card metric action-card" onClick={() => setView('weekly')}><span>周测 Gate</span><strong>周测</strong><p>听写与朗读 gate：决定是否推荐下一轮</p></button>
        <button className="card metric action-card" onClick={() => setView('candidates')}><span>候选素材 · P1</span><strong>候选</strong><p>搜索音质清晰的 15–20 分钟候选，确认后创建正式素材</p></button>
        <button className="card metric action-card" onClick={() => setView('p2')}><span>长期仪表盘 · P2</span><strong>仪表盘</strong><p>时长 / 首次理解 / 记忆深化 / 难度历史</p></button>
      </section>

      <section className="materials">
        <div className="section-heading">
          <div><p className="eyebrow">训练库</p><h3>最近素材</h3></div>
          <span>{materials.length ? '状态已同步' : '等待导入素材'}</span>
        </div>
        {materials.length ? (
          <div className="material-list">
            {materials
              .slice()
              .sort((a, b) => progressRank(a.current_state) - progressRank(b.current_state))
              .slice(0, 5)
              .map((material) => (
                <button className="material-row" key={material.material_id} onClick={() => openMaterial(material.material_id)}>
                  <div><strong>{material.title}</strong><p>{material.material_id}</p></div>
                  <span>{material.current_state || material.status}</span>
                </button>
              ))}
          </div>
        ) : <p className="empty">导入预置素材后，这里会显示当前 Part 和恢复位置。</p>}
      </section>
      </>}

      {view === 'materials' && <section className="module module-materials">
        <button className="back" onClick={() => setView('home')}>← 返回总览</button>
        <div className="section-heading light"><div><p className="eyebrow">声音训练素材</p><h2>搜索并进入训练</h2></div><button className="secondary" onClick={() => setShowImport(!showImport)}>{showImport ? '关闭导入' : '导入素材'}</button></div>
        <form className="search-bar" onSubmit={search}><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、编号或 transcript" /><button className="primary" type="submit">搜索</button></form><p className="search-hint">当前搜索范围：本地已导入素材；外部素材源将在 MaterialProvider 接入后开放。</p>
        {showImport && <form className="import-form" onSubmit={createMaterial}><input required placeholder="素材 ID，如 lesson-001" value={form.material_id} onChange={(event) => setForm({ ...form, material_id: event.target.value })} /><input required placeholder="素材标题" value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /><input required placeholder="音频路径（本地文件）" value={form.audio_path} onChange={(event) => setForm({ ...form, audio_path: event.target.value })} /><textarea required placeholder="每行一句，至少 3 行" value={form.transcript} onChange={(event) => setForm({ ...form, transcript: event.target.value })} /><button className="primary" type="submit">保存并进入训练</button></form>}
        {message && <p className="notice">{message}</p>}
        <div className="result-list">{materials.length ? materials.map((material) => <button className="result-row" key={material.material_id} onClick={() => openMaterial(material.material_id)}><div><strong>{material.title}</strong><p>{material.material_id} · {Math.round(material.duration_seconds)} 秒</p></div><span>{material.current_state || material.status} →</span></button>) : <p className="empty light-text">没有匹配素材。可以先导入一个预置素材。</p>}</div>
      </section>}

      {view === 'weekly' && <section className="module module-weekly">
        <button className="back" onClick={() => setView('home')}>← 返回总览</button>
        <div className="section-heading light"><div><p className="eyebrow">周测 Gate</p><h2>每周质量闸门</h2><p className="muted">根据当周训练内容生成测试；低于 80% 或朗读未达标时不推荐进入下一轮，转入短强化包。</p></div></div>
        <WeeklyPanel onMessage={setMessage} />
        {message && <p className="notice">{message}</p>}
      </section>}

      {view === 'candidates' && <section className="module module-candidates">
        <button className="back" onClick={() => setView('home')}>← 返回总览</button>
        <div className="section-heading light"><div><p className="eyebrow">候选素材（P1）</p><h2>自动获取素材</h2><p className="muted">候选先经音质三档分级与 Transcript 校验，你确认后才准备为正式素材。</p></div></div>
        <CandidatePanel
          stage={candidateStage}
          onStageChange={setCandidateStage}
          onMessage={setMessage}
          onPrepared={onMaterialImported}
        />
        {message && <p className="notice">{message}</p>}
      </section>}

      {view === 'p2' && <section className="module module-p2">
        <button className="back" onClick={() => setView('home')}>← 返回总览</button>
        <div className="section-heading light"><div><p className="eyebrow">长期训练仪表盘（P2）</p><h2>观察与解释层</h2><p className="muted">只读视图：时长 / 首次理解曲线 / 周测趋势 / 朗读三维 / 记忆深化 / 难度历史。</p></div></div>
        <P2Dashboard onMessage={setMessage} />
        {message && <p className="notice">{message}</p>}
      </section>}

      {view === 'training' && <section className="module training-module">
        <button className="back" onClick={() => setView('materials')}>← 返回素材列表</button>
        {selected && <><div className="section-heading light"><div><p className="eyebrow">声音训练</p><h2>{selected.title}</h2><p className="muted">{selected.material_id} · {selected.sentences.length} 句 · {selected.source_name || '预置素材'}</p></div><div className="training-actions"><span className="state-badge">{selected.current_state}</span><button className="skip-btn" onClick={skipMaterial}>跳过此素材，换一篇</button></div></div>
        <div className="progress-matrix-wrap"><p className="eyebrow">流程状态</p>{progressMatrix()}</div>
        <div className="audio-panel"><p className="eyebrow">素材音频</p><audio controls preload="metadata" src={`/api/materials/${encodeURIComponent(selected.material_id)}/audio`} onEnded={() => setFirstListenPlayed(true)} /><p className="muted">如果播放器提示找不到文件，请检查导入时填写的本地音频路径。</p></div>
        <div className="training-panel"><h3>继续训练</h3>{trainingBody()}</div>{message && <p className="notice">{message}</p>}</>}
      </section>}
    </main>
  )
}

export default App
