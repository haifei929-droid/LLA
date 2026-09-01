import { useEffect, useState } from 'react'
import DictationPanel from './DictationPanel.jsx'
import ReadingPanel from './ReadingPanel.jsx'
import WeeklyPanel from './WeeklyPanel.jsx'
import CandidatePanel from './CandidatePanel.jsx'
import P2Dashboard from './P2Dashboard.jsx'

const blankMaterial = { material_id: '', title: '', audio_path: '', transcript: '' }

const COMPLETED_STATES = ['LISTENING_COMPLETED', 'FULLY_COMPLETED']

const STATE_TEXT = {
  READY_FIRST_LISTEN: '首次盲听',
  FIRST_COMPREHENSION_CHECK: '理解检查',
  DICTATION_PART_1: '听写 Part 1',
  DICTATION_PART_2: '听写 Part 2',
  DICTATION_PART_3: '听写 Part 3',
  SECOND_FULL_LISTEN: '二次复听',
  SECOND_COMPREHENSION_CHECK: '理解复测',
  READING_AVAILABLE: '朗读训练',
  FULL_READING_ASSESSMENT: '全文朗读验收',
  LISTENING_COMPLETED: '听力完成',
  FULLY_COMPLETED: '已完成',
}

const VIEW_TITLES = {
  home: '训练工作台',
  materials: '素材',
  weekly: '周测 Gate',
  candidates: '候选素材',
  p2: '长期仪表盘',
  training: '训练',
}

const TRAINING_MODES = [
  { key: 'blind', name: '盲听', desc: '完整播放，不看原文' },
  { key: 'dictation', name: '听写', desc: '逐句精听，逐字校对' },
  { key: 'reading', name: '朗读', desc: '跟读训练与朗读评分' },
  { key: 'weekly', name: '周测', desc: '每周质量闸门与强化' },
]

function progressRank(state) {
  if (!state || COMPLETED_STATES.includes(state)) return 1
  return 0
}

function stateText(state) {
  return STATE_TEXT[state] || state || '未开始'
}

function App() {
  const [health, setHealth] = useState('连接检查中…')
  const [materials, setMaterials] = useState([])
  const [view, setView] = useState('home')
  const [candidateStage, setCandidateStage] = useState('STAGE_1')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const [showImport, setShowImport] = useState(false)
  const [form, setForm] = useState(blankMaterial)
  const [message, setMessage] = useState('')
  const [comprehension, setComprehension] = useState({ rating: '30–50%', summary: '' })
  const [dictationContext, setDictationContext] = useState(null)
  const [dictationLoading, setDictationLoading] = useState(false)
  const [firstListenPlayed, setFirstListenPlayed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [homeError, setHomeError] = useState('')

  const refresh = () => {
    setLoading(true)
    setHomeError('')
    return fetch('/api/materials')
      .then((response) => {
        if (!response.ok) throw new Error('materials')
        return response.json()
      })
      .then((payload) => {
        setMaterials(payload)
        setLoading(false)
        return payload
      })
      .catch((error) => {
        setLoading(false)
        setHomeError('当前训练暂时无法加载，请检查连接后重试。')
        throw error
      })
  }

  useEffect(() => {
    fetch('/api/health')
      .then((response) => response.json())
      .then((healthPayload) => {
        setHealth(healthPayload.status === 'ok' ? '训练核心已就绪' : '训练核心异常')
        return refresh()
      })
      .catch(() => {
        setHealth('后端尚未启动')
        setLoading(false)
        setHomeError('当前训练暂时无法加载，请检查连接后重试。')
      })
  }, [])

  // 当前训练素材：优先最近一个进行中的素材，否则最近一篇（可能已完成）。
  const inProgress = materials.filter((material) => material.current_state && !COMPLETED_STATES.includes(material.current_state))
  const currentMaterial = inProgress[0] || materials[0] || null
  const currentCompleted = currentMaterial ? COMPLETED_STATES.includes(currentMaterial.current_state) : false

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

  const openMode = (key) => {
    if (key === 'weekly') { setView('weekly'); return }
    if (currentMaterial) openMaterial(currentMaterial.material_id)
    else setView('materials')
  }

  const retry = () => {
    setLoading(true)
    fetch('/api/health')
      .then((response) => response.json())
      .then((payload) => {
        setHealth(payload.status === 'ok' ? '训练核心已就绪' : '训练核心异常')
        return refresh()
      })
      .catch(() => {
        setHealth('后端尚未启动')
        setLoading(false)
        setHomeError('当前训练暂时无法加载，请检查连接后重试。')
      })
  }

  const refreshDictationContext = () => {
    if (!selected || !selected.current_state?.startsWith('DICTATION_PART_')) return
    setDictationLoading(true)
    fetch(`/api/materials/${selected.material_id}/dictation-context`)
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail || '无法加载听写上下文')
        return payload
      })
      .then((context) => {
        setDictationContext(context)
        setDictationLoading(false)
      })
      .catch((error) => {
        setMessage(error.message)
        setDictationLoading(false)
      })
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
        refresh().catch(() => {})
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
        refresh().then(() => openMaterial(payload.material_id)).catch(() => {})
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
      if (dictationLoading) return <p className="muted">正在加载听写上下文…</p>
      if (!dictationContext) {
        return <div className="event-panel">
          <p className="muted">听写上下文加载失败，可重试。</p>
          <button className="primary" onClick={refreshDictationContext}>重新加载</button>
        </div>
      }
      return <DictationPanel
        materialId={selected.material_id}
        context={dictationContext}
        onAdvance={refreshDictationContext}
        onPartComplete={() => {
          const part = Number(state.slice(-1))
          fetch(`/api/materials/${selected.material_id}/dictation-parts/${part}/complete`, { method: 'POST' })
            .then(async (response) => {
              const payload = await response.json()
              if (!response.ok) throw new Error(payload.detail || 'Part 完成失败')
              return payload
            })
            .then((progress) => {
              setSelected((current) => current ? { ...current, ...progress } : current)
              setDictationContext(null)
              refresh().catch(() => {})
              setMessage('Part 完成，进入下一步。')
            })
            .catch((error) => setMessage(error.message))
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

  const navItems = [
    { key: 'home', label: '首页' },
    { key: 'materials', label: '素材' },
    { key: 'weekly', label: '周测' },
    { key: 'candidates', label: '候选' },
    { key: 'p2', label: '仪表盘' },
  ]

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">LLA</div>
        <nav className="sidebar-nav">
          {navItems.map((item) => (
            <button key={item.key} className={view === item.key ? 'active' : ''} aria-current={view === item.key ? 'page' : undefined} onClick={() => setView(item.key)}>{item.label}</button>
          ))}
        </nav>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div>
            <p className="eyebrow">LANGUAGE TRAINING AGENT</p>
            <h1 className="page-title">{VIEW_TITLES[view] || '训练'}</h1>
          </div>
          <span className="status">{health}</span>
        </header>

        {view === 'home' && <>
          {health === '后端尚未启动' && <div className="notice error">后端尚未启动，无法加载训练数据。<button className="link-btn" onClick={retry}>重试</button></div>}
          <section className="home-section accent-training" aria-busy={loading}>
            <p className="section-label">当前训练</p>
            {loading ? (
              <div className="training-hero skeleton-block" role="status" aria-label="正在加载当前训练">
                <div className="sk-line sk-short"></div>
                <div className="sk-line sk-wide"></div>
                <div className="sk-line"></div>
              </div>
            ) : homeError ? (
              <div className="training-hero error-hero" role="alert">
                <div className="hero-meta"><span className="hero-state">加载失败</span></div>
                <h2>当前训练暂时不可用</h2>
                <p className="hero-context">{homeError}</p>
                <button className="primary" onClick={retry}>重试</button>
              </div>
            ) : currentMaterial ? (
              <div className="training-hero">
                <div className="hero-meta">
                  <span className="hero-state">{stateText(currentMaterial.current_state)}</span>
                  {currentMaterial.duration_seconds ? <span className="hero-muted">约 {Math.round(currentMaterial.duration_seconds / 60)} 分钟</span> : null}
                  {currentMaterial.speech_rate_wpm ? <span className="hero-muted">{currentMaterial.speech_rate_wpm} wpm</span> : null}
                </div>
                <h2>{currentMaterial.title}</h2>
                <p className="hero-context">{currentMaterial.material_id}</p>
                <button className="primary" onClick={() => openMaterial(currentMaterial.material_id)}>
                  {currentCompleted ? '查看素材' : '继续训练'}
                </button>
              </div>
            ) : (
              <div className="training-hero empty-hero">
                <h2>还没有开始训练</h2>
                <p className="hero-context">导入或获取一篇素材，开始第一次盲听。</p>
                <button className="primary" onClick={() => setView('materials')}>开始新素材</button>
              </div>
            )}
          </section>

          <section className="home-section accent-material">
            <p className="section-label">当前素材 / 新素材</p>
            <div className="material-cards">
              {currentMaterial ? (
                <button className="material-card" onClick={() => openMaterial(currentMaterial.material_id)}>
                  <span className="card-tag">当前素材</span>
                  <strong>{currentMaterial.title}</strong>
                  <p>{stateText(currentMaterial.current_state)}</p>
                  <span className="card-action">{currentCompleted ? '查看 →' : '继续 →'}</span>
                </button>
              ) : (
                <div className="material-card is-empty">
                  <span className="card-tag">当前素材</span>
                  <strong>暂无素材</strong>
                  <p>导入素材后，这里显示当前进度。</p>
                </div>
              )}
              <button className="material-card" onClick={() => setView('candidates')}>
                <span className="card-tag">新素材</span>
                <strong>自动获取素材</strong>
                <p>搜索音质清晰的候选，确认后创建。</p>
                <span className="card-action">去获取 →</span>
              </button>
            </div>
          </section>

          <section className="home-section accent-modes">
            <p className="section-label">训练方式</p>
            <div className="mode-cards">
              {TRAINING_MODES.map((mode) => (
                <button key={mode.key} className="mode-card" onClick={() => openMode(mode.key)}>
                  <strong>{mode.name}</strong>
                  <p>{mode.desc}</p>
                </button>
              ))}
            </div>
          </section>
        </>}

        {view === 'materials' && <section className="module">
          <div className="section-heading light"><div><p className="eyebrow">声音训练素材</p><h2>搜索并进入训练</h2></div><button className="secondary" onClick={() => setShowImport(!showImport)}>{showImport ? '关闭导入' : '导入素材'}</button></div>
          <form className="search-bar" onSubmit={search}><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、编号或 transcript" /><button className="primary" type="submit">搜索</button></form><p className="search-hint">当前搜索范围：本地已导入素材；外部素材源将在 MaterialProvider 接入后开放。</p>
          {showImport && <form className="import-form" onSubmit={createMaterial}><input required placeholder="素材 ID，如 lesson-001" value={form.material_id} onChange={(event) => setForm({ ...form, material_id: event.target.value })} /><input required placeholder="素材标题" value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /><input required placeholder="音频路径（本地文件）" value={form.audio_path} onChange={(event) => setForm({ ...form, audio_path: event.target.value })} /><textarea required placeholder="每行一句，至少 3 行" value={form.transcript} onChange={(event) => setForm({ ...form, transcript: event.target.value })} /><button className="primary" type="submit">保存并进入训练</button></form>}
          {message && <p className="notice">{message}</p>}
          <div className="result-list">{materials.length ? materials.map((material) => <button className="result-row" key={material.material_id} onClick={() => openMaterial(material.material_id)}><div><strong>{material.title}</strong><p>{material.material_id} · {Math.round(material.duration_seconds)} 秒</p></div><span>{material.current_state || material.status} →</span></button>) : <p className="empty light-text">没有匹配素材。可以先导入一个预置素材。</p>}</div>
        </section>}

        {view === 'weekly' && <section className="module">
          <div className="section-heading light"><div><p className="eyebrow">周测 Gate</p><h2>每周质量闸门</h2><p className="muted">根据当周训练内容生成测试；低于 80% 或朗读未达标时不推荐进入下一轮，转入短强化包。</p></div></div>
          <WeeklyPanel onMessage={setMessage} />
          {message && <p className="notice">{message}</p>}
        </section>}

        {view === 'candidates' && <section className="module">
          <div className="section-heading light"><div><p className="eyebrow">候选素材（P1）</p><h2>自动获取素材</h2><p className="muted">候选先经音质三档分级与 Transcript 校验，你确认后才准备为正式素材。</p></div></div>
          <CandidatePanel
            stage={candidateStage}
            onStageChange={setCandidateStage}
            onMessage={setMessage}
            onPrepared={onMaterialImported}
          />
          {message && <p className="notice">{message}</p>}
        </section>}

        {view === 'p2' && <section className="module">
          <div className="section-heading light"><div><p className="eyebrow">长期训练仪表盘（P2）</p><h2>观察与解释层</h2><p className="muted">只读视图：时长 / 首次理解曲线 / 周测趋势 / 朗读三维 / 记忆深化 / 难度历史。</p></div></div>
          <P2Dashboard onMessage={setMessage} />
          {message && <p className="notice">{message}</p>}
        </section>}

        {view === 'training' && <section className="module training-module">
          {selected && <><div className="section-heading light"><div><p className="eyebrow">声音训练</p><h2>{selected.title}</h2><p className="muted">{selected.material_id} · {selected.sentences.length} 句 · {selected.source_name || '预置素材'}</p></div><div className="training-actions"><span className="state-badge">{selected.current_state}</span><button className="skip-btn" onClick={skipMaterial}>跳过此素材，换一篇</button></div></div>
          <div className="progress-matrix-wrap"><p className="eyebrow">流程状态</p>{progressMatrix()}</div>
          <div className="audio-panel"><p className="eyebrow">素材音频</p><audio controls preload="metadata" src={`/api/materials/${encodeURIComponent(selected.material_id)}/audio`} onEnded={() => setFirstListenPlayed(true)} /><p className="muted">如果播放器提示找不到文件，请检查导入时填写的本地音频路径。</p></div>
          <div className="training-panel"><h3>继续训练</h3>{trainingBody()}</div>{message && <p className="notice">{message}</p>}</>}
        </section>}
      </main>
    </div>
  )
}

export default App
