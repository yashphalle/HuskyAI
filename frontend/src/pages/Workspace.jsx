import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate, useSearchParams, Navigate, useLocation } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Sidebar from '../components/Sidebar'
import SessionAnalysisCard from '../components/SessionAnalysisCard'
import { SAMPLE_EVAL, cannedAssistantReply, DEMO_CHALLENGE_CONTEXTS } from '../demo/demoData'

const WS_BASE = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws'

/* ─── Score helpers ─── */
function scoreColor(pei) {
  if (pei <= 40) return '#C8102E'
  if (pei <= 65) return '#F97316'
  if (pei <= 80) return '#0D9488'
  return '#16A34A'
}
function scoreBg(pei) {
  if (pei <= 40) return '#FDE8EC'
  if (pei <= 65) return '#FEF3E8'
  if (pei <= 80) return '#E6F7F6'
  return '#DCFCE7'
}
function scoreLabel(pei) {
  if (pei <= 40) return 'Novice'
  if (pei <= 65) return 'Developing'
  if (pei <= 80) return 'Practitioner'
  return 'Expert'
}

const DIM_META = {
  PSQ: { label: 'Prompt Quality',       color: '#C8102E' },
  CCM: { label: 'Conversation Control', color: '#F97316' },
  TSI: { label: 'Tech Sophistication',  color: '#0D9488' },
  CLM: { label: 'Cognitive Load',       color: '#7C3AED' },
  RAS: { label: 'Reliance Calibration', color: '#D97706' },
}

/* ─── PEI Ring ─── */
function PeiRing({ pei = 0 }) {
  const r = 50, circ = Math.PI * 2 * r
  const offset = circ - (pei / 100) * circ
  const col = scoreColor(pei)
  return (
    <div className="relative w-[120px] h-[120px] mx-auto mb-3">
      <svg viewBox="0 0 120 120" className="w-[120px] h-[120px] -rotate-90">
        <circle cx="60" cy="60" r={r} fill="none" stroke="#E7E0D8" strokeWidth="9" />
        <circle cx="60" cy="60" r={r} fill="none" stroke={col} strokeWidth="9"
          strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={offset}
          className="ring-arc" />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-serif text-[28px] text-[#16120E] leading-none">{Math.round(pei)}</span>
        <span className="text-[10px] font-bold text-[#9A948E] uppercase tracking-[0.5px] mt-0.5">Husky Score</span>
      </div>
    </div>
  )
}

/* ─── Dimension bar ─── */
const DIM_DESC = {
  PSQ: 'Verb clarity, context, constraints & focus',
  CCM: 'Initiative, verification & course correction',
  TSI: 'Decomposition, tool awareness & edge cases',
  CLM: 'Chunk size, incremental building & clarity',
  RAS: 'Trust calibration & correct reliance',
}

function DimBar({ code, value = 0, max = 100 }) {
  const meta = DIM_META[code] || { label: code, color: '#9A948E' }
  const pct = Math.min(100, (value / max) * 100)
  return (
    <div className="mb-[14px]">
      <div className="flex items-center gap-[10px] mb-[4px]">
        <div className="flex items-center gap-[6px] flex-1 min-w-0">
          <span className="text-[11px] font-bold font-mono px-1.5 py-0.5 rounded flex-shrink-0" style={{ color: meta.color, background: `${meta.color}15`, border: `1px solid ${meta.color}30` }}>{code}</span>
          <span className="text-[12px] text-[#4A4440] font-medium truncate">{meta.label}</span>
        </div>
        <div className="text-[12px] font-bold text-[#4A4440] w-8 text-right flex-shrink-0">{Math.round(value)}</div>
      </div>
      <div className="flex items-center gap-[10px]">
        <div className="flex-1 h-[6px] bg-[#F7F3EE] rounded-full border border-[#E7E0D8] overflow-hidden">
          <div className="h-full rounded-full prog-fill" style={{ width: `${pct}%`, background: meta.color }} />
        </div>
      </div>
      <div className="text-[11px] text-[#9A948E] mt-[3px]">{DIM_DESC[code]}</div>
    </div>
  )
}

/* Format a millisecond remainder as M:SS for the session countdown. */
function fmtClock(ms) {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/* ─── Eval Panel ─── */
function EvalSidebar({ evalData, isEvaluating, turnCount }) {
  const pei = evalData?.scores?.PEI ?? 0
  const scores = evalData?.scores || {}
  const suggestions = evalData?.suggestions || []
  const classification = evalData?.classification || '-'
  const leadStatus = evalData?.leading_status || '-'

  return (
    <div className="h-full bg-[#FDFCFB] border-l border-[#E7E0D8] flex flex-col overflow-y-auto" style={{ borderLeftWidth: '1.5px' }}>
      {/* Header */}
      <div className="px-5 py-4 border-b border-[#E7E0D8] flex items-center justify-between flex-shrink-0" style={{ borderBottomWidth: '1.5px' }}>
        <div>
          <div className="text-[11px] font-bold text-[#9A948E] uppercase tracking-[0.7px]">Prompt Evaluator</div>
          {turnCount > 0 && <div className="text-[12px] text-[#9A948E] mt-0.5">Turn {turnCount}</div>}
        </div>
        {isEvaluating && (
          <div className="flex items-center gap-1.5 text-[11px] text-[#9A948E]">
            <div className="w-1.5 h-1.5 rounded-full bg-[#C8102E] live-dot" />
            Scoring…
          </div>
        )}
      </div>

      <div className="p-5 flex flex-col gap-4 flex-1">
        {/* PEI ring */}
        <div className="bg-[#FDFCFB] border border-[#E7E0D8] rounded-[14px] p-5 text-center" style={{ borderWidth: '1.5px' }}>
          <PeiRing pei={pei} />
          <div className="flex items-center justify-center gap-2 mb-1.5">
            <span className="text-[11px] font-bold px-[10px] py-[3px] rounded-[20px]"
              style={{ background: scoreBg(pei), color: scoreColor(pei) }}>
              {scoreLabel(pei)}
            </span>
          </div>
          {classification !== '-' && (
            <div className="text-[12px] text-[#9A948E]">{classification} · {leadStatus}</div>
          )}
        </div>

        {/* Dimension breakdown */}
        <div className="bg-[#FDFCFB] border border-[#E7E0D8] rounded-[14px] p-5" style={{ borderWidth: '1.5px' }}>
          <div className="text-[11px] font-bold text-[#9A948E] uppercase tracking-[0.7px] mb-[14px]">Dimension Scores</div>
          {Object.keys(DIM_META).map(k => (
            <DimBar key={k} code={k} value={scores[k] ?? 0} max={100} />
          ))}
        </div>

        {/* Suggestions */}
        {suggestions.length > 0 && (
          <div className="bg-[#FDFCFB] border border-[#E7E0D8] rounded-[14px] p-5" style={{ borderWidth: '1.5px' }}>
            <div className="text-[11px] font-bold text-[#9A948E] uppercase tracking-[0.7px] mb-3">Coach Suggestions</div>
            {suggestions.map((s, i) => (
              <div key={i} className="flex items-start gap-[8px] bg-[#FEF9EC] border border-[#FDE68A] rounded-[10px] p-3 mb-2 last:mb-0">
                <svg className="w-[14px] h-[14px] flex-shrink-0 mt-[1px]" viewBox="0 0 24 24" fill="none" stroke="#D97706" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
                </svg>
                <p className="text-[12px] text-[#92400E] leading-[1.6]">{s}</p>
              </div>
            ))}
          </div>
        )}

        {/* Empty state */}
        {!evalData && !isEvaluating && (
          <div className="flex-1 flex flex-col items-center justify-center text-center py-10">
            <div className="w-10 h-10 rounded-full bg-[#F7F3EE] flex items-center justify-center mb-3">
              <svg className="w-5 h-5 text-[#9A948E]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
              </svg>
            </div>
            <div className="text-[13px] font-medium text-[#4A4440] mb-1">No evaluation yet</div>
            <div className="text-[12px] text-[#9A948E]">Send a message to get scored</div>
          </div>
        )}
      </div>
    </div>
  )
}

/* ─── Message bubble ─── */
function Message({ role, content }) {
  const isUser = role === 'user'
  return (
    <div className={`flex gap-3 message-enter ${isUser ? 'justify-end' : 'justify-start'}`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-[#C8102E] flex items-center justify-center flex-shrink-0 mt-0.5">
          <svg className="w-3.5 h-3.5 stroke-white fill-none" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="20" cy="16" r="2"/>
            <path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.045Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10Z"/>
          </svg>
        </div>
      )}
      <div className={`max-w-[75%] px-4 py-3 rounded-[14px] text-[14px] leading-[1.65] border ${
        isUser
          ? 'bg-[#EDEAE4] border-[#E7E0D8] text-[#16120E] rounded-br-[4px]'
          : 'bg-[#FDFCFB] border-[#E7E0D8] text-[#16120E] rounded-bl-[4px]'
      }`} style={{ borderWidth: '1.5px' }}>
        {isUser
          ? <p>{content}</p>
          : <div className="prose-chat"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></div>
        }
      </div>
      {isUser && (
        <div className="w-7 h-7 rounded-full bg-[#E7E0D8] flex items-center justify-center flex-shrink-0 mt-0.5 text-[11px] font-bold text-[#4A4440]">
          Y
        </div>
      )}
    </div>
  )
}

/* ─── Challenge brief banner ─── */
function ChallengeBanner({ context, onUseSeed }) {
  const [expanded, setExpanded] = useState(true)
  if (!context) return null
  return (
    <div style={{
      background: '#FDE8EC',
      border: '1.5px solid #F9BFCA',
      borderRadius: '12px',
      padding: '14px 16px',
      margin: '0 0 12px',
      flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: expanded ? '10px' : 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#C8102E" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
          </svg>
          <span style={{ fontSize: '12px', fontWeight: 700, color: '#C8102E' }}>Challenge: {context.title}</span>
        </div>
        <button
          onClick={() => setExpanded(e => !e)}
          style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '11px', color: '#9E0B24', fontWeight: 600 }}
        >
          {expanded ? 'Hide' : 'Show brief'}
        </button>
      </div>
      {expanded && (
        <>
          <p style={{ fontSize: '12px', color: '#9E0B24', marginBottom: '8px', lineHeight: 1.6 }}>
            <strong>Goal:</strong> {context.goal}
          </p>
          <div style={{ background: 'rgba(255,255,255,0.6)', borderRadius: '8px', padding: '10px', fontSize: '12px', color: '#4A4440', lineHeight: 1.7, marginBottom: '10px', whiteSpace: 'pre-wrap' }}>
            {context.brief}
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '10px' }}>
            <p style={{ fontSize: '12px', color: '#9E0B24', fontStyle: 'italic', flex: 1, margin: 0, lineHeight: 1.6 }}>
              "{context.seed_question}"
            </p>
            <button
              onClick={() => onUseSeed(context.seed_question)}
              style={{ padding: '6px 12px', background: '#C8102E', color: '#fff', border: 'none', borderRadius: '6px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', flexShrink: 0 }}
            >
              Use this →
            </button>
          </div>
        </>
      )}
    </div>
  )
}

/* ─── Main Workspace ─── */
export default function Workspace() {
  const navigate = useNavigate()
  const location = useLocation()
  const isDemo = location.pathname.startsWith('/demo')
  const [searchParams] = useSearchParams()
  const challengeId  = searchParams.get('challenge')
  const sessionNum   = searchParams.get('session')
  const demoChallengeSlug = searchParams.get('demoChallenge')

  const token = localStorage.getItem('token')
  const user = isDemo
    ? { name: 'Demo Student', email: 'demo@husky.edu' }
    : JSON.parse(localStorage.getItem('user') || 'null')

  const [messages, setMessages]           = useState([])
  const [streamingContent, setStreaming]  = useState('')
  const [isStreaming, setIsStreaming]     = useState(false)
  const [isTyping, setIsTyping]           = useState(false)
  const [isEvaluating, setIsEvaluating]  = useState(false)
  const [evalData, setEvalData]           = useState(null)
  const [connStatus, setConnStatus]       = useState('disconnected')
  const [turnCount, setTurnCount]         = useState(0)
  const [input, setInput]                 = useState('')
  const [challengeContext, setChallengeContext] = useState(null)
  const [briefExpanded, setBriefExpanded] = useState(true)
  const [conversationId, setConversationId] = useState(null)
  const [sessionEnded, setSessionEnded]   = useState(false)
  const [endingSession, setEndingSession] = useState(false)
  const [sessionScore, setSessionScore]   = useState(null)
  const [endReason, setEndReason]         = useState(null) // "manual" | "timer_expired" | null
  // Post-session analysis (generated in the background on the server; polled after /end).
  const [showAnalysis, setShowAnalysis]   = useState(false)
  const [analysis, setAnalysis]           = useState(null)
  const [analysisLoading, setAnalysisLoading] = useState(false)
  // Timed-session state. minTurns / deadlineMs are null when the challenge is untimed.
  const [minTurns, setMinTurns]           = useState(null)
  const [deadlineMs, setDeadlineMs]       = useState(null)
  const [remainingMs, setRemainingMs]     = useState(null)
  const [timeWarning, setTimeWarning]     = useState(false)

  const wsRef              = useRef(null)
  const reconnectTimer     = useRef(null)
  const sessionEndedRef    = useRef(false)
  const warnedRef          = useRef(false)
  const autoEndRef         = useRef(false)
  const streamBuffer       = useRef('')
  const messagesEndRef     = useRef(null)
  const textareaRef        = useRef(null)

  const userInitials = user?.name ? user.name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase() : 'U'

  const handleLogout = () => {
    if (isDemo) {
      wsRef.current?.close()
      navigate('/', { replace: true })
      return
    }
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    wsRef.current?.close()
    navigate('/login', { replace: true })
  }

  const handleWsMessage = useCallback((data) => {
    switch (data.type) {
      case 'session_init':
        setConversationId(data.conversation_id)
        setMinTurns(typeof data.min_turns === 'number' ? data.min_turns : null)
        if (typeof data.turn_count === 'number') setTurnCount(data.turn_count)
        if (typeof data.remaining_seconds === 'number') {
          // Anchor the countdown to the client clock at connect; the server still
          // enforces the real cutoff, so small skew is harmless.
          warnedRef.current = false
          setTimeWarning(false)
          setDeadlineMs(Date.now() + data.remaining_seconds * 1000)
          setRemainingMs(data.remaining_seconds * 1000)
        } else {
          setDeadlineMs(null)
          setRemainingMs(null)
        }
        break
      case 'session_ended':
        sessionEndedRef.current = true
        setSessionEnded(true)
        break
      case 'challenge_context':
        setChallengeContext(data.data)
        break
      case 'history':
        if (Array.isArray(data.messages)) {
          setMessages(
            data.messages
              .filter(m => m && typeof m.role === 'string' && typeof m.content === 'string')
              .map(m => ({ role: m.role, content: m.content })),
          )
        }
        if (typeof data.turn_count === 'number') setTurnCount(data.turn_count)
        break
      case 'typing':
        setIsTyping(true); setIsStreaming(false)
        streamBuffer.current = ''; setStreaming('')
        break
      case 'stream':
        setIsTyping(false); setIsStreaming(true)
        streamBuffer.current += data.content
        setStreaming(prev => prev + data.content)
        break
      case 'done':
        setIsStreaming(false); setIsTyping(false)
        setMessages(prev => [...prev, { role: 'assistant', content: data.full_response || streamBuffer.current }])
        streamBuffer.current = ''; setStreaming('')
        break
      case 'eval_start': setIsEvaluating(true); break
      case 'eval':
        setIsEvaluating(false)
        setEvalData(data.data)
        setTurnCount(t => t + 1)
        // Tell the Sidebar (and anyone else who cares) the Husky Score may have shifted
        try { window.dispatchEvent(new CustomEvent('husky:eval')) } catch {}
        break
      case 'eval_error': setIsEvaluating(false); break
      case 'error':
        setIsStreaming(false); setIsTyping(false); setIsEvaluating(false)
        console.error('Server error:', data.message); break
      default: break
    }
  }, [])

  const connect = useCallback(() => {
    if (isDemo || !token) return
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    setConnStatus('connecting')
    let wsUrl = `${WS_BASE}?token=${token}`
    if (challengeId) wsUrl += `&challenge_id=${challengeId}`
    if (sessionNum)  wsUrl += `&session_num=${sessionNum}`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws
    ws.onopen  = () => { setConnStatus('connected'); clearTimeout(reconnectTimer.current) }
    ws.onclose = (e) => {
      setConnStatus('disconnected'); setIsStreaming(false); setIsTyping(false); setIsEvaluating(false)
      if (e.code === 4001) { handleLogout(); return }
      if (sessionEndedRef.current) return
      reconnectTimer.current = setTimeout(connect, 3000)
    }
    ws.onerror = () => setConnStatus('error')
    ws.onmessage = (e) => { try { handleWsMessage(JSON.parse(e.data)) } catch {} }
  }, [token, challengeId, sessionNum, handleWsMessage, isDemo])

  useEffect(() => {
    if (isDemo) {
      setConnStatus('connected')
      return () => { clearTimeout(reconnectTimer.current); wsRef.current?.close() }
    }
    if (!token) { navigate('/login', { replace: true }); return }
    connect()
    return () => { clearTimeout(reconnectTimer.current); wsRef.current?.close() }
  }, [connect, isDemo, token, navigate])

  useEffect(() => {
    if (!isDemo || !demoChallengeSlug) return
    const ctx = DEMO_CHALLENGE_CONTEXTS[demoChallengeSlug]
    if (ctx) setChallengeContext(ctx)
  }, [isDemo, demoChallengeSlug])

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, streamingContent])

  // Poll the post-session analysis until it's ready or failed (it generates in
  // the background on the server). Capped so a stuck job doesn't poll forever.
  const pollAnalysis = useCallback(async (convId) => {
    if (isDemo || !convId) return
    const apiBase = import.meta.env.VITE_API_URL || 'http://localhost:8000'
    setAnalysisLoading(true)
    for (let attempt = 0; attempt < 20; attempt++) {
      try {
        const resp = await fetch(`${apiBase}/conversations/${convId}/analysis`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (resp.ok) {
          const data = await resp.json().catch(() => ({}))
          if (data && (data.status === 'ready' || data.status === 'failed')) {
            setAnalysis(data)
            setAnalysisLoading(false)
            return
          }
          setAnalysis(data)
        }
      } catch (e) {
        console.error('Failed to fetch session analysis', e)
      }
      await new Promise((r) => setTimeout(r, 3000))
    }
    setAnalysisLoading(false)
  }, [isDemo, token])

  const handleRetryAnalysis = useCallback(async () => {
    if (isDemo || !conversationId) return
    const apiBase = import.meta.env.VITE_API_URL || 'http://localhost:8000'
    setAnalysis({ status: 'pending' })
    try {
      await fetch(`${apiBase}/conversations/${conversationId}/analysis/retry`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
    } catch (e) {
      console.error('Failed to retry analysis', e)
    }
    pollAnalysis(conversationId)
  }, [isDemo, conversationId, token, pollAnalysis])

  const handleEndSession = async ({ auto = false } = {}) => {
    if (isDemo || !conversationId) return
    if (!auto && (sessionEnded || endingSession)) return
    setEndingSession(true)
    let ok = false
    try {
      const apiBase = import.meta.env.VITE_API_URL || 'http://localhost:8000'
      const resp = await fetch(`${apiBase}/conversations/${conversationId}/end`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (resp.ok) {
        const data = await resp.json().catch(() => ({}))
        setSessionScore(data.session_avg_pei ?? null)
        setEndReason(data.end_reason ?? null)
        ok = true
        // Surface the analysis panel and start polling for the background result.
        setShowAnalysis(true)
        pollAnalysis(conversationId)
      }
    } catch (e) {
      console.error('Failed to end session', e)
    } finally {
      setEndingSession(false)
    }
    // Lock on success; on a timer auto-end also lock even if the request hiccuped
    // (the server enforces the cutoff regardless, so the UI must reflect it).
    if (ok || auto) {
      sessionEndedRef.current = true
      setSessionEnded(true)
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }

  // Live countdown for timed sessions: tick each second, warn at 1 minute, auto-end at 0.
  useEffect(() => {
    if (!deadlineMs || sessionEnded || isDemo) return
    let cancelled = false
    const tick = () => {
      if (cancelled) return
      const rem = deadlineMs - Date.now()
      setRemainingMs(rem)
      if (rem <= 60000 && rem > 0 && !warnedRef.current) {
        warnedRef.current = true
        setTimeWarning(true)
      }
      if (rem <= 0 && !autoEndRef.current) {
        autoEndRef.current = true
        handleEndSession({ auto: true })
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { cancelled = true; clearInterval(id) }
    // handleEndSession is intentionally excluded; the autoEndRef guard makes it fire once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deadlineMs, sessionEnded, isDemo])

  const handleSend = useCallback(() => {
    const content = input.trim()
    if (!content || isStreaming || isTyping || isEvaluating) return
    if (isDemo) {
      setMessages(prev => [...prev, { role: 'user', content }])
      setInput('')
      if (textareaRef.current) textareaRef.current.style.height = 'auto'
      setIsTyping(true)
      window.setTimeout(() => {
        setIsTyping(false)
        const reply = cannedAssistantReply(content)
        setMessages(prev => [...prev, { role: 'assistant', content: reply }])
        setIsEvaluating(true)
        window.setTimeout(() => {
          setEvalData(SAMPLE_EVAL)
          setIsEvaluating(false)
          setTurnCount((t) => t + 1)
        }, 450)
      }, 550)
      return
    }
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    setMessages(prev => [...prev, { role: 'user', content }])
    wsRef.current.send(JSON.stringify({ type: 'message', content }))
    setInput('')
    if (textareaRef.current) { textareaRef.current.style.height = 'auto' }
  }, [input, isStreaming, isTyping, isEvaluating, isDemo])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleTextarea = (e) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px'
  }

  if (!isDemo && !token) return <Navigate to="/login" replace />

  const connDot = { connected: '#16A34A', connecting: '#F97316', error: '#C8102E', disconnected: '#9A948E' }

  return (
    <div className="flex h-screen bg-[#F7F3EE] overflow-hidden">
      <Sidebar onLogout={handleLogout} />

      {/* Main area */}
      <div className="flex-1 flex flex-col overflow-hidden" style={{ marginLeft: '220px' }}>

        {/* Topbar */}
        <div className="h-14 bg-[#FDFCFB] border-b border-[#E7E0D8] flex items-center px-8 gap-3 flex-shrink-0 sticky top-0 z-10" style={{ borderBottomWidth: '1.5px' }}>
          {challengeId ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <button
                onClick={() => navigate(`${isDemo ? '/demo' : ''}/challenges/${challengeId}`)}
                style={{ display: 'flex', alignItems: 'center', gap: '5px', background: 'none', border: 'none', cursor: 'pointer', color: '#9A948E', fontSize: '12px', padding: 0 }}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                Challenge
              </button>
              <span style={{ color: '#E7E0D8' }}>/</span>
              <span className="text-[14px] font-semibold text-[#16120E]">
                {challengeContext?.title || 'Session ' + sessionNum}
              </span>
              {sessionNum && (
                <span style={{ fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '20px', background: '#FDE8EC', color: '#C8102E' }}>
                  Session {sessionNum}
                </span>
              )}
            </div>
          ) : (
            <div>
              <span className="text-[15px] font-semibold text-[#16120E]">
                {isDemo ? 'Workspace (demo)' : 'Workspace'}
              </span>
              <span className="text-[12px] text-[#9A948E] ml-2">
                {isDemo ? 'Sample coach + scoring' : 'Free Practice'}
              </span>
            </div>
          )}
          <div className="ml-auto flex items-center gap-3">
            {/* Live countdown chip (timed sessions only) */}
            {!isDemo && !sessionEnded && remainingMs != null && (
              <div
                style={{
                  display: 'flex', alignItems: 'center', gap: '5px',
                  padding: '4px 10px', borderRadius: '8px',
                  fontSize: '12px', fontWeight: 700, fontVariantNumeric: 'tabular-nums',
                  background: timeWarning ? '#FEF3E8' : '#F7F3EE',
                  color: timeWarning ? '#C2410C' : '#6B6560',
                  border: `1px solid ${timeWarning ? '#FED7AA' : '#E7E0D8'}`,
                }}
                title={timeWarning ? 'Session ends soon' : 'Time remaining in this session'}
              >
                <span aria-hidden>⏱</span>{fmtClock(remainingMs)}
              </div>
            )}
            {conversationId && !isDemo && (() => {
              const minTurnsMet = minTurns == null || turnCount >= minTurns
              const turnsLeft = minTurns != null ? Math.max(0, minTurns - turnCount) : 0
              const disabled = sessionEnded || endingSession || !minTurnsMet
              return (
                <button
                  onClick={() => handleEndSession()}
                  disabled={disabled}
                  title={!minTurnsMet && !sessionEnded
                    ? `Send ${turnsLeft} more turn${turnsLeft !== 1 ? 's' : ''} to end`
                    : undefined}
                  style={{
                    padding: '5px 14px',
                    background: disabled ? '#F7F3EE' : '#FDE8EC',
                    color: disabled ? '#9A948E' : '#C8102E',
                    border: '1.5px solid',
                    borderColor: disabled ? '#E7E0D8' : '#F9BFCA',
                    borderRadius: '8px',
                    fontSize: '12px',
                    fontWeight: 600,
                    cursor: disabled ? 'default' : 'pointer',
                    opacity: endingSession ? 0.6 : 1,
                  }}
                >
                  {sessionEnded
                    ? 'Session Ended'
                    : endingSession
                      ? 'Ending…'
                      : !minTurnsMet
                        ? `End Session (${turnCount}/${minTurns})`
                        : 'End Session'}
                </button>
              )
            })()}
            <div className="flex items-center gap-1.5 text-[12px] text-[#9A948E]">
              <div className="w-[6px] h-[6px] rounded-full" style={{ background: connDot[connStatus] }} />
              {isDemo
                ? 'Demo mode'
                : connStatus === 'connected'
                  ? 'Connected'
                  : connStatus === 'connecting'
                    ? 'Connecting…'
                    : 'Disconnected'}
            </div>
          </div>
        </div>

        {/* Two-panel layout */}
        <div className="flex-1 flex overflow-hidden">

          {/* Chat panel */}
          <div className="flex flex-col flex-1 overflow-hidden bg-[#F7F3EE]">
            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-6 py-5 flex flex-col gap-4">
              {/* Challenge brief banner */}
              {challengeContext && (
                <ChallengeBanner
                  context={challengeContext}
                  onUseSeed={(q) => { setInput(q); textareaRef.current?.focus() }}
                />
              )}

              {messages.length === 0 && !isTyping && !isStreaming && (
                <div className="flex-1 flex flex-col items-center justify-center text-center py-20">
                  <div className="w-12 h-12 bg-[#C8102E] rounded-[14px] flex items-center justify-center mb-4 mx-auto">
                    <svg className="w-6 h-6 stroke-white fill-none" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="20" cy="16" r="2"/>
                      <path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.045Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10Z"/>
                    </svg>
                  </div>
                  <h2 className="font-serif text-[22px] text-[#16120E] mb-2">
                    {challengeContext ? 'Challenge ready' : 'Start your session'}
                  </h2>
                  <p className="text-[13px] text-[#9A948E] max-w-[320px] leading-[1.65]">
                    {challengeContext
                      ? 'Click "Use this →" above to load the seed question, or write your own.'
                      : 'Ask a question. Your prompting style will be scored in real time.'}
                  </p>
                </div>
              )}

              {messages.map((m, i) => <Message key={i} role={m.role} content={m.content} />)}

              {/* Typing / streaming */}
              {isTyping && (
                <div className="flex gap-3 justify-start message-enter">
                  <div className="w-7 h-7 rounded-full bg-[#C8102E] flex items-center justify-center flex-shrink-0 mt-0.5">
                    <svg className="w-3.5 h-3.5 stroke-white fill-none" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="20" cy="16" r="2"/>
                      <path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.045Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10Z"/>
                    </svg>
                  </div>
                  <div className="px-4 py-3 rounded-[14px] rounded-bl-[4px] bg-[#FDFCFB] border border-[#E7E0D8]" style={{ borderWidth: '1.5px' }}>
                    <div className="flex gap-1 items-center h-5">
                      {[0, 0.2, 0.4].map(d => (
                        <div key={d} className="w-1.5 h-1.5 rounded-full bg-[#9A948E]" style={{ animation: `livePulse 1.2s ${d}s infinite` }} />
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {isStreaming && streamingContent && (
                <div className="flex gap-3 justify-start message-enter">
                  <div className="w-7 h-7 rounded-full bg-[#C8102E] flex items-center justify-center flex-shrink-0 mt-0.5">
                    <svg className="w-3.5 h-3.5 stroke-white fill-none" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="20" cy="16" r="2"/>
                      <path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.045Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10Z"/>
                    </svg>
                  </div>
                  <div className="max-w-[75%] px-4 py-3 rounded-[14px] rounded-bl-[4px] bg-[#FDFCFB] border border-[#E7E0D8] text-[14px] leading-[1.65] text-[#16120E]" style={{ borderWidth: '1.5px' }}>
                    <div className="prose-chat"><ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingContent}</ReactMarkdown></div>
                    <span className="typing-cursor" />
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* Input area */}
            <div className="flex-shrink-0 px-6 py-4 border-t border-[#E7E0D8] bg-[#FDFCFB]" style={{ borderTopWidth: '1.5px' }}>
              {sessionEnded ? (
                <div style={{
                  background: '#F7F3EE',
                  border: '1.5px solid #E7E0D8',
                  borderRadius: '12px',
                  padding: '14px 18px',
                  textAlign: 'center',
                  color: '#9A948E',
                  fontSize: '13px',
                  fontWeight: 500,
                }}>
                  Session ended
                  {endReason === 'timer_expired' && (
                    <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '20px', background: '#FEF3E8', color: '#C2410C', marginLeft: '8px' }}>
                      Timed out
                    </span>
                  )}
                  {endReason === 'manual' && (
                    <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '20px', background: '#F7F3EE', color: '#6B6560', marginLeft: '8px' }}>
                      Ended by you
                    </span>
                  )}
                  {sessionScore != null && (
                    <span> · Session avg PEI: <strong style={{ color: '#C8102E' }}>{sessionScore}</strong></span>
                  )}
                  {' '}— saved to your Husky Score.
                  <div style={{ marginTop: '10px' }}>
                    <button
                      type="button"
                      onClick={() => setShowAnalysis(true)}
                      style={{
                        background: '#C8102E', color: '#fff', border: 'none', borderRadius: '8px',
                        padding: '7px 16px', fontSize: '13px', fontWeight: 600, cursor: 'pointer',
                      }}
                    >
                      View session analysis
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex gap-3 items-end bg-[#FDFCFB] border border-[#E7E0D8] rounded-[14px] px-4 py-3" style={{ borderWidth: '1.5px' }}>
                  <textarea
                    ref={textareaRef}
                    value={input}
                    onChange={handleTextarea}
                    onKeyDown={handleKeyDown}
                    placeholder={challengeContext ? 'Respond to the challenge brief… (Shift+Enter for new line)' : 'Ask a question… (Shift+Enter for new line)'}
                    rows={1}
                    className="flex-1 resize-none outline-none bg-transparent text-[14px] text-[#16120E] placeholder-[#9A948E] leading-[1.6] max-h-[160px] font-sans"
                    style={{ fontFamily: "'DM Sans', sans-serif" }}
                  />
                  <button
                    onClick={handleSend}
                    disabled={!input.trim() || isStreaming || isTyping || isEvaluating || (!isDemo && connStatus !== 'connected')}
                    className="w-9 h-9 rounded-[9px] bg-[#C8102E] hover:bg-[#9E0B24] disabled:opacity-40 flex items-center justify-center flex-shrink-0 transition-colors border-none cursor-pointer"
                  >
                    <svg className="w-4 h-4 stroke-white fill-none" viewBox="0 0 24 24" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
                    </svg>
                  </button>
                </div>
              )}
              {!sessionEnded && (
                <div className="text-[11px] text-[#9A948E] mt-2 text-center">
                  Powered by Gemini 2.5 Pro · Prompts are evaluated for learning purposes
                </div>
              )}
            </div>
          </div>

          {/* Eval panel */}
          <div className="w-[380px] flex-shrink-0 overflow-hidden">
            <EvalSidebar evalData={evalData} isEvaluating={isEvaluating} turnCount={turnCount} />
          </div>
        </div>
      </div>

      {/* Post-session analysis modal */}
      {showAnalysis && !isDemo && (
        <div
          onClick={() => setShowAnalysis(false)}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(22,18,14,0.42)', backdropFilter: 'blur(4px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: '24px',
            animation: 'fadeIn 0.2s ease',
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: '#FDFCFB', borderRadius: '16px', border: '1.5px solid #E7E0D8',
              width: '100%', maxWidth: '560px', maxHeight: '85vh', overflowY: 'auto',
              padding: '26px 28px', boxShadow: '0 24px 60px rgba(0,0,0,0.22)',
              animation: 'modalPop 0.28s cubic-bezier(0.22,1,0.36,1)',
            }}
          >
            <style>{`@keyframes fadeIn{from{opacity:0}to{opacity:1}}@keyframes modalPop{from{opacity:0;transform:translateY(12px) scale(0.98)}to{opacity:1;transform:none}}`}</style>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '4px' }}>
              <button
                type="button"
                onClick={() => setShowAnalysis(false)}
                aria-label="Close"
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '20px', color: '#9A948E', lineHeight: 1 }}
              >
                ×
              </button>
            </div>
            <SessionAnalysisCard analysis={analysis} loading={analysisLoading} onRetry={handleRetryAnalysis} />
          </div>
        </div>
      )}
    </div>
  )
}
