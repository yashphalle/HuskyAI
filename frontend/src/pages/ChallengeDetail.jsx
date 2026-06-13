import { useState, useEffect } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import SessionAnalysisCard from '../components/SessionAnalysisCard'
import { getDemoChallengeDetail, demoSlugForChallengeId } from '../demo/demoData'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const CATEGORY_STYLES = {
  'Technical':          { color: '#C8102E', bg: '#FDE8EC' },
  'Creative & Strategy':{ color: '#7C3AED', bg: '#F5F3FF' },
  'Data & Analysis':    { color: '#0D9488', bg: '#E6F7F6' },
  'Product & Business': { color: '#D97706', bg: '#FEF9EC' },
}
const DIFF_STYLES = {
  'Beginner':     { color: '#16A34A', bg: '#DCFCE7' },
  'Intermediate': { color: '#F97316', bg: '#FEF3E8' },
  'Advanced':     { color: '#C8102E', bg: '#FDE8EC' },
}

function statusColor(s) {
  if (s === 'completed') return { text: '#16A34A', bg: '#DCFCE7', label: 'Completed' }
  if (s === 'in_progress') return { text: '#C8102E', bg: '#FDE8EC', label: 'In progress' }
  return { text: '#9A948E', bg: '#F7F3EE', label: 'Not started' }
}

// How a completed session ended (server-decided). Null for older sessions
// completed before end_reason was tracked — no badge shown in that case.
function endReasonBadge(r) {
  if (r === 'timer_expired') return { text: '#C2410C', bg: '#FEF3E8', label: 'Timed out' }
  if (r === 'manual') return { text: '#6B6560', bg: '#F7F3EE', label: 'Ended by you' }
  return null
}

export default function ChallengeDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const isDemo = location.pathname.startsWith('/demo')
  const pathPrefix = isDemo ? '/demo' : ''
  const [challenge, setChallenge] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(null)   // session number being started
  const [completing, setCompleting] = useState(null) // session number being marked complete
  // Post-session analysis modal (revisit a completed session's analysis).
  const [analysisOpen, setAnalysisOpen] = useState(false)
  const [analysisData, setAnalysisData] = useState(null)
  const [analysisLoading, setAnalysisLoading] = useState(false)

  const [analysisConvId, setAnalysisConvId] = useState(null)

  const pollAnalysis = async (conversationId) => {
    const token = localStorage.getItem('token')
    setAnalysisLoading(true)
    // Usually ready for a completed session; poll briefly in case it's still generating.
    for (let attempt = 0; attempt < 10; attempt++) {
      try {
        const resp = await fetch(`${API_URL}/conversations/${conversationId}/analysis`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (resp.ok) {
          const data = await resp.json().catch(() => ({}))
          setAnalysisData(data)
          if (data && (data.status === 'ready' || data.status === 'failed' || data.status === 'none')) {
            setAnalysisLoading(false)
            return
          }
        }
      } catch (e) {
        console.error('Failed to fetch session analysis', e)
      }
      await new Promise((r) => setTimeout(r, 3000))
    }
    setAnalysisLoading(false)
  }

  const openAnalysis = (conversationId) => {
    if (!conversationId) return
    setAnalysisData(null)
    setAnalysisConvId(conversationId)
    setAnalysisOpen(true)
    pollAnalysis(conversationId)
  }

  const handleRetryAnalysis = async () => {
    if (!analysisConvId) return
    const token = localStorage.getItem('token')
    setAnalysisData({ status: 'pending' })
    try {
      await fetch(`${API_URL}/conversations/${analysisConvId}/analysis/retry`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
    } catch (e) {
      console.error('Failed to retry analysis', e)
    }
    pollAnalysis(analysisConvId)
  }

  const handleLogout = () => {
    if (isDemo) {
      navigate('/', { replace: true })
      return
    }
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    navigate('/login', { replace: true })
  }

  const loadChallenge = () => {
    if (isDemo) {
      const d = getDemoChallengeDetail(id)
      if (d) { setChallenge(d); setError('') }
      else setError('Challenge not found')
      setLoading(false)
      return
    }
    const token = localStorage.getItem('token')
    fetch(`${API_URL}/challenges/${id}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => {
        if (!r.ok) throw new Error('Not found')
        return r.json()
      })
      .then(data => setChallenge(data))
      .catch(() => setError('Challenge not found'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadChallenge() }, [id, isDemo]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleStartSession = async (sessionNumber) => {
    setStarting(sessionNumber)
    try {
      if (isDemo) {
        const slug = demoSlugForChallengeId(id)
        navigate(`${pathPrefix}/workspace?demoChallenge=${encodeURIComponent(slug)}`)
        return
      }
      const token = localStorage.getItem('token')
      const res = await fetch(`${API_URL}/challenges/${id}/sessions/${sessionNumber}/start`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        const d = await res.json()
        alert(d.detail || 'Could not start session')
        return
      }
      navigate(`/workspace?challenge=${id}&session=${sessionNumber}`)
    } catch {
      alert('Failed to start session')
    } finally {
      setStarting(null)
    }
  }

  const handleCompleteSession = async (sessionNumber) => {
    if (isDemo) return
    setCompleting(sessionNumber)
    try {
      const token = localStorage.getItem('token')
      const res = await fetch(`${API_URL}/challenges/${id}/sessions/${sessionNumber}/complete`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        alert(d.detail || 'Could not mark session as complete')
        return
      }
      // Reload so progress ring, status badges, and unlock state all update
      setLoading(true)
      loadChallenge()
    } catch {
      alert('Failed to complete session')
    } finally {
      setCompleting(null)
    }
  }

  if (loading) {
    return (
      <div className="flex h-screen bg-[#F7F3EE] overflow-hidden">
        <Sidebar onLogout={handleLogout} />
        <div style={{ marginLeft: '220px', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <span style={{ color: '#9A948E', fontSize: '14px' }}>Loading...</span>
        </div>
      </div>
    )
  }

  if (error || !challenge) {
    return (
      <div className="flex h-screen bg-[#F7F3EE] overflow-hidden">
        <Sidebar onLogout={handleLogout} />
        <div style={{ marginLeft: '220px', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: '12px' }}>
          <span style={{ color: '#C8102E', fontSize: '14px' }}>{error || 'Not found'}</span>
          <button onClick={() => navigate(`${pathPrefix}/challenges`)} style={{ fontSize: '13px', color: '#C8102E', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>
            Back to challenges
          </button>
        </div>
      </div>
    )
  }

  const cs = CATEGORY_STYLES[challenge.category] || { color: '#4A4440', bg: '#F7F3EE' }
  const ds = DIFF_STYLES[challenge.difficulty] || { color: '#4A4440', bg: '#F7F3EE' }
  const completedCount = challenge.sessions.filter(s => s.status === 'completed').length
  const progress = Math.round((completedCount / challenge.total_sessions) * 100)

  return (
    <div className="flex h-screen bg-[#F7F3EE] overflow-hidden">
      <Sidebar onLogout={handleLogout} />
      <div className="flex-1 flex flex-col overflow-hidden" style={{ marginLeft: '220px' }}>

        {/* Topbar */}
        <div className="h-14 bg-[#FDFCFB] border-b border-[#E7E0D8] flex items-center px-8 gap-3 flex-shrink-0" style={{ borderBottomWidth: '1.5px' }}>
          <button
            onClick={() => navigate(`${pathPrefix}/challenges`)}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'none', border: 'none', cursor: 'pointer', color: '#9A948E', fontSize: '13px', padding: 0 }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            Challenges
          </button>
          <span style={{ color: '#E7E0D8' }}>/</span>
          <span style={{ fontSize: '14px', fontWeight: 600, color: '#16120E' }}>{challenge.title}</span>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          <div style={{ maxWidth: '860px', margin: '0 auto', padding: '32px 32px' }}>

            {/* Hero card */}
            <div style={{
              background: '#FDFCFB',
              borderRadius: '16px',
              padding: '28px',
              border: '1.5px solid #E7E0D8',
              marginBottom: '24px',
            }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px', marginBottom: '16px' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', gap: '8px', marginBottom: '12px', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '11px', fontWeight: 700, padding: '3px 10px', borderRadius: '20px', background: cs.bg, color: cs.color }}>{challenge.category}</span>
                    <span style={{ fontSize: '11px', fontWeight: 700, padding: '3px 10px', borderRadius: '20px', background: ds.bg, color: ds.color }}>{challenge.difficulty}</span>
                    {challenge.week && (
                      <span style={{ fontSize: '11px', fontWeight: 600, padding: '3px 10px', borderRadius: '20px', background: '#F7F3EE', color: '#9A948E', border: '1px solid #E7E0D8' }}>Week {challenge.week}</span>
                    )}
                    {(challenge.time_limit_minutes != null || challenge.min_turns != null) && (
                      <span style={{ fontSize: '11px', fontWeight: 700, padding: '3px 10px', borderRadius: '20px', background: '#FEF3E8', color: '#C2410C', border: '1px solid #FED7AA' }}>
                        {challenge.time_limit_minutes != null ? `⏱ ${challenge.time_limit_minutes} min` : ''}
                        {challenge.time_limit_minutes != null && challenge.min_turns != null ? ' · ' : ''}
                        {challenge.min_turns != null ? `${challenge.min_turns} turns min` : ''}
                      </span>
                    )}
                  </div>
                  <h1 style={{ fontSize: '22px', fontWeight: 600, color: '#16120E', fontFamily: "'Instrument Serif', serif", margin: 0, lineHeight: 1.3 }}>
                    {challenge.title}
                  </h1>
                </div>
                {/* Progress ring */}
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
                  <svg width="56" height="56" viewBox="0 0 56 56">
                    <circle cx="28" cy="28" r="22" fill="none" stroke="#F7F3EE" strokeWidth="5" />
                    <circle
                      cx="28" cy="28" r="22"
                      fill="none"
                      stroke={progress === 100 ? '#16A34A' : '#C8102E'}
                      strokeWidth="5"
                      strokeDasharray={`${(progress / 100) * 138} 138`}
                      strokeDashoffset="0"
                      strokeLinecap="round"
                      transform="rotate(-90 28 28)"
                    />
                    <text x="28" y="33" textAnchor="middle" fontSize="12" fontWeight="700" fill="#16120E">{progress}%</text>
                  </svg>
                  <span style={{ fontSize: '10px', color: '#9A948E' }}>{completedCount}/{challenge.total_sessions} done</span>
                </div>
              </div>

              <p style={{ fontSize: '13px', color: '#4A4440', lineHeight: 1.7, margin: 0 }}>
                {challenge.description}
              </p>
            </div>

            {/* Sessions list */}
            <h2 style={{ fontSize: '13px', fontWeight: 700, color: '#9A948E', textTransform: 'uppercase', letterSpacing: '0.8px', marginBottom: '12px', marginTop: 0 }}>
              Sessions
            </h2>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {challenge.sessions.map((session, idx) => {
                const sc = statusColor(session.status)
                const prevCompleted = idx === 0 || challenge.sessions[idx - 1].status === 'completed'
                const isLocked = session.status === 'not_started' && !prevCompleted
                const canStart = !isLocked && session.status !== 'completed'
                const isActive = starting === session.session_number

                return (
                  <div
                    key={session.session_number}
                    style={{
                      background: '#FDFCFB',
                      borderRadius: '12px',
                      padding: '20px',
                      border: '1.5px solid',
                      borderColor: session.status === 'in_progress' ? '#F9BFCA' : isLocked ? '#F7F3EE' : '#E7E0D8',
                      opacity: isLocked ? 0.55 : 1,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '16px' }}>
                      {/* Step number */}
                      <div style={{
                        width: '32px', height: '32px', borderRadius: '50%', flexShrink: 0,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        background: session.status === 'completed' ? '#DCFCE7' : session.status === 'in_progress' ? '#FDE8EC' : '#F7F3EE',
                        border: '1.5px solid',
                        borderColor: session.status === 'completed' ? '#16A34A' : session.status === 'in_progress' ? '#C8102E' : '#E7E0D8',
                      }}>
                        {session.status === 'completed' ? (
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#16A34A" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                        ) : isLocked ? (
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#9A948E" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                          </svg>
                        ) : (
                          <span style={{ fontSize: '12px', fontWeight: 700, color: session.status === 'in_progress' ? '#C8102E' : '#9A948E' }}>
                            {session.session_number}
                          </span>
                        )}
                      </div>

                      {/* Content */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px', gap: '12px' }}>
                          <span style={{ fontSize: '14px', fontWeight: 600, color: '#16120E' }}>
                            Session {session.session_number}: {session.title}
                          </span>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                            {session.status === 'completed' && (() => {
                              const er = endReasonBadge(session.end_reason)
                              return er ? (
                                <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '20px', background: er.bg, color: er.text }}>
                                  {er.label}
                                </span>
                              ) : null
                            })()}
                            <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '20px', background: sc.bg, color: sc.text }}>
                              {sc.label}
                            </span>
                          </div>
                        </div>

                        <p style={{ fontSize: '12px', color: '#9A948E', margin: '0 0 8px', lineHeight: 1.6 }}>
                          <strong style={{ color: '#4A4440' }}>Goal:</strong> {session.goal}
                        </p>

                        <div style={{
                          background: '#F7F3EE',
                          borderRadius: '8px',
                          padding: '12px',
                          fontSize: '12px',
                          color: '#4A4440',
                          lineHeight: 1.7,
                          whiteSpace: 'pre-wrap',
                          marginBottom: session.best_pei != null || canStart ? '12px' : 0,
                        }}>
                          {session.brief}
                        </div>

                        {/* Seed question hint */}
                        {!isLocked && (
                          <div style={{
                            padding: '10px 12px',
                            background: '#FDE8EC',
                            borderRadius: '8px',
                            fontSize: '12px',
                            color: '#9E0B24',
                            lineHeight: 1.6,
                            marginBottom: canStart ? '12px' : 0,
                            fontStyle: 'italic',
                          }}>
                            <strong style={{ fontStyle: 'normal', color: '#C8102E' }}>Starting prompt: </strong>
                            {session.seed_question}
                          </div>
                        )}

                        {/* Meta + actions */}
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px', flexWrap: 'wrap' }}>
                          {session.best_pei != null && (
                            <span style={{ fontSize: '12px', color: '#9A948E' }}>
                              Best PEI: <strong style={{ color: '#C8102E' }}>{Math.round(session.best_pei)}</strong>
                            </span>
                          )}

                          <div style={{ display: 'flex', gap: '8px', marginLeft: 'auto', flexWrap: 'wrap' }}>
                            {/* in_progress: Continue + Mark as complete */}
                            {session.status === 'in_progress' && (
                              <>
                                <button
                                  disabled={completing === session.session_number}
                                  onClick={() => handleCompleteSession(session.session_number)}
                                  style={{
                                    padding: '8px 16px',
                                    background: 'transparent',
                                    color: completing === session.session_number ? '#9A948E' : '#16A34A',
                                    border: `1.5px solid ${completing === session.session_number ? '#E7E0D8' : '#16A34A'}`,
                                    borderRadius: '8px',
                                    fontSize: '13px',
                                    fontWeight: 600,
                                    cursor: completing === session.session_number ? 'not-allowed' : 'pointer',
                                  }}
                                >
                                  {completing === session.session_number ? 'Saving…' : 'Mark as complete'}
                                </button>
                                <button
                                  disabled={isActive}
                                  onClick={() => handleStartSession(session.session_number)}
                                  style={{
                                    padding: '8px 20px',
                                    background: '#C8102E',
                                    color: '#fff',
                                    border: 'none',
                                    borderRadius: '8px',
                                    fontSize: '13px',
                                    fontWeight: 600,
                                    cursor: isActive ? 'not-allowed' : 'pointer',
                                    opacity: isActive ? 0.7 : 1,
                                  }}
                                >
                                  {isActive ? 'Starting...' : 'Continue session'}
                                </button>
                              </>
                            )}

                            {/* not_started (and not locked): Start session */}
                            {session.status === 'not_started' && !isLocked && (
                              <button
                                disabled={isActive}
                                onClick={() => handleStartSession(session.session_number)}
                                style={{
                                  padding: '8px 20px',
                                  background: '#C8102E',
                                  color: '#fff',
                                  border: 'none',
                                  borderRadius: '8px',
                                  fontSize: '13px',
                                  fontWeight: 600,
                                  cursor: isActive ? 'not-allowed' : 'pointer',
                                  opacity: isActive ? 0.7 : 1,
                                }}
                              >
                                {isActive ? 'Starting...' : 'Start session'}
                              </button>
                            )}

                            {/* completed: Review */}
                            {session.status === 'completed' && (
                              <button
                                onClick={() => {
                                  if (isDemo) {
                                    const slug = demoSlugForChallengeId(id)
                                    navigate(`${pathPrefix}/workspace?demoChallenge=${encodeURIComponent(slug)}`)
                                  } else {
                                    navigate(`/workspace?challenge=${id}&session=${session.session_number}`)
                                  }
                                }}
                                style={{
                                  padding: '8px 20px',
                                  background: 'transparent',
                                  color: '#4A4440',
                                  border: '1.5px solid #E7E0D8',
                                  borderRadius: '8px',
                                  fontSize: '13px',
                                  fontWeight: 600,
                                  cursor: 'pointer',
                                }}
                              >
                                Review session
                              </button>
                            )}

                            {/* completed: View analysis (real sessions with a saved conversation) */}
                            {session.status === 'completed' && !isDemo && session.conversation_id && (
                              <button
                                onClick={() => openAnalysis(session.conversation_id)}
                                style={{
                                  padding: '8px 20px',
                                  background: '#C8102E',
                                  color: '#fff',
                                  border: 'none',
                                  borderRadius: '8px',
                                  fontSize: '13px',
                                  fontWeight: 600,
                                  cursor: 'pointer',
                                }}
                              >
                                View analysis
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

          </div>
        </div>
      </div>

      {/* Post-session analysis modal */}
      {analysisOpen && (
        <div
          onClick={() => setAnalysisOpen(false)}
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
                onClick={() => setAnalysisOpen(false)}
                aria-label="Close"
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '20px', color: '#9A948E', lineHeight: 1 }}
              >
                ×
              </button>
            </div>
            <SessionAnalysisCard analysis={analysisData} loading={analysisLoading} onRetry={handleRetryAnalysis} />
          </div>
        </div>
      )}
    </div>
  )
}
