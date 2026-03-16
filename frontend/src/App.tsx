// frontend/src/App.tsx

import { useState, useEffect, useRef, useCallback } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from 'reactflow'
import dagre from 'dagre'
import 'reactflow/dist/style.css'
import './App.css'

type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED'
type EdgeType  = 'direct' | 'relative'

interface RawNode { id: string; label: string }
interface RawEdge { source: string; target: string; type: EdgeType }
interface GraphData { nodes: RawNode[]; edges: RawEdge[] }

interface JobResponse {
  job_id:         string
  status:         JobStatus
  repo_url:       string
  graph_data?:    GraphData
  error_message?: string
}

interface SubmitResponse {
  job_id: string
  cached: boolean
}

const NODE_WIDTH  = 200
const NODE_HEIGHT = 40
const POLL_INTERVAL_MS = 3000

function applyDagreLayout(nodes: RawNode[], edges: RawEdge[]) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 100 })
  nodes.forEach(n => g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT }))
  edges.forEach(e => g.setEdge(e.source, e.target))
  dagre.layout(g)

  const rfNodes: Node[] = nodes.map(n => {
    const pos = g.node(n.id)
    return {
      id: n.id,
      data: { label: n.label },
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
      style: {
        background: '#1a202c', border: '1px solid #4f8ef7',
        color: '#e2e8f0', borderRadius: 6, fontSize: 11,
        padding: '6px 10px', width: NODE_WIDTH,
      },
    }
  })

  const rfEdges: Edge[] = edges.map((e, i) => ({
    id: `e-${i}`,
    source: e.source,
    target: e.target,
    style: e.type === 'relative'
      ? { stroke: '#68d391', strokeWidth: 1.5, strokeDasharray: '6 3' }
      : { stroke: '#4f8ef7', strokeWidth: 1.5 },
    label: e.type === 'relative' ? 'rel' : undefined,
    labelStyle: { fill: '#68d391', fontSize: 9 },
  }))

  return { rfNodes, rfEdges }
}

export default function App() {
  const [repoUrl, setRepoUrl]           = useState('')
  const [jobId, setJobId]               = useState<string | null>(null)
  const [jobStatus, setJobStatus]       = useState<JobStatus | null>(null)
  const [graphData, setGraphData]       = useState<GraphData | null>(null)
  const [summary, setSummary]           = useState('')
  const [summaryDone, setSummaryDone]   = useState(false)
  const [errorMsg, setErrorMsg]         = useState<string | null>(null)
  const [submitting, setSubmitting]     = useState(false)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [fromCache, setFromCache]       = useState<boolean | null>(null)

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const esRef   = useRef<EventSource | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  const pollJob = useCallback(async (id: string) => {
    try {
      const res  = await fetch(`/api/jobs/${id}/`)
      const data: JobResponse = await res.json()
      setJobStatus(data.status)
      if (data.status === 'COMPLETED') {
        setGraphData(data.graph_data ?? null)
        stopPolling()
      } else if (data.status === 'FAILED') {
        setErrorMsg(data.error_message ?? 'Analysis failed.')
        stopPolling()
      }
    } catch {
      setErrorMsg('Network error while polling.')
      stopPolling()
    }
  }, [stopPolling])

  useEffect(() => {
    if (!jobId) return
    pollRef.current = setInterval(() => pollJob(jobId), POLL_INTERVAL_MS)
    pollJob(jobId)
    return stopPolling
  }, [jobId, pollJob, stopPolling])

  // SSE — only open stream for fresh jobs; cached jobs already have summary in DB
  useEffect(() => {
    if (jobStatus !== 'COMPLETED' || !jobId || fromCache) return
    esRef.current?.close()
    const es = new EventSource(`/api/jobs/${jobId}/summary-stream/`)
    esRef.current = es

    es.onmessage = evt => setSummary(prev => prev + evt.data.replace(/\\n/g, '\n'))
    es.addEventListener('done',  () => { setSummaryDone(true); es.close() })
    es.addEventListener('error', (evt) => {
      setSummary(prev => prev || `[Summary unavailable: ${(evt as MessageEvent).data}]`)
      setSummaryDone(true)
      es.close()
    })
    return () => es.close()
  }, [jobStatus, jobId, fromCache])

  // For cached jobs, summary comes directly from the poll response
  useEffect(() => {
    if (jobStatus !== 'COMPLETED' || !fromCache) return
    const fetchSummary = async () => {
      const res  = await fetch(`/api/jobs/${jobId}/`)
      const data = await res.json()
      setSummary(data.summary ?? '')
      setSummaryDone(true)
    }
    fetchSummary()
  }, [jobStatus, fromCache, jobId])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!repoUrl.trim()) return

    setJobId(null); setJobStatus(null); setGraphData(null)
    setSummary(''); setSummaryDone(false); setErrorMsg(null)
    setSelectedNode(null); setFromCache(null)
    setSubmitting(true)
    esRef.current?.close()

    try {
      const res = await fetch('/api/jobs/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_url: repoUrl.trim() }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.error ?? 'Failed to submit job.')
      }
      const data: SubmitResponse = await res.json()
      setJobId(data.job_id)
      setFromCache(data.cached)
      setJobStatus(data.cached ? 'COMPLETED' : 'PENDING')
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Submission failed.')
    } finally {
      setSubmitting(false)
    }
  }

  const handleNodeClick: NodeMouseHandler = useCallback((_evt, node) => {
    setSelectedNode(prev => prev === node.id ? null : node.id)
  }, [])

  const nodeImports = selectedNode && graphData
    ? graphData.edges.filter(e => e.source === selectedNode).map(e => ({ target: e.target, type: e.type }))
    : []

  const { rfNodes, rfEdges } = graphData
    ? applyDagreLayout(graphData.nodes, graphData.edges)
    : { rfNodes: [], rfEdges: [] }

  const styledNodes = rfNodes.map(n => ({
    ...n,
    style: {
      ...n.style,
      border:     n.id === selectedNode ? '2px solid #f6e05e' : '1px solid #4f8ef7',
      background: n.id === selectedNode ? '#2d3748' : '#1a202c',
    },
  }))

  const isBusy = submitting || jobStatus === 'PENDING' || jobStatus === 'PROCESSING'

  return (
    <div className="app">
      <h1>🔍 RepoInsight</h1>

      <form className="form" onSubmit={handleSubmit}>
        <input
          type="url"
          placeholder="https://github.com/owner/repo"
          value={repoUrl}
          onChange={e => setRepoUrl(e.target.value)}
          disabled={isBusy}
        />
        <button type="submit" disabled={isBusy}>
          {submitting ? 'Submitting…' : 'Analyse'}
        </button>
      </form>

      <div className="status-row">
        {jobStatus && (
          <span className={`status-badge ${jobStatus}`}>
            {jobStatus === 'PENDING'    && '⏳ Pending'}
            {jobStatus === 'PROCESSING' && '⚙️ Processing'}
            {jobStatus === 'COMPLETED'  && '✅ Completed'}
            {jobStatus === 'FAILED'     && '❌ Failed'}
          </span>
        )}
        {jobStatus === 'COMPLETED' && fromCache !== null && (
          <span className={`cache-badge ${fromCache ? 'cached' : 'fresh'}`}>
            {fromCache ? '⚡ Loaded from Cache' : '🔬 Freshly Analyzed'}
          </span>
        )}
      </div>

      {errorMsg && (
        <div className="error-box"><strong>Error:</strong> {errorMsg}</div>
      )}

      {jobStatus === 'COMPLETED' && graphData && graphData.nodes.length > 0 && (
        <div className="results">
          <div className="legend">
            <span className="legend-item"><span className="legend-line solid" /> Direct import</span>
            <span className="legend-item"><span className="legend-line dashed" /> Relative import</span>
          </div>

          <div className="graph-row">
            <div className="graph-container">
              <ReactFlow
                nodes={styledNodes}
                edges={rfEdges}
                onNodeClick={handleNodeClick}
                fitView
                attributionPosition="bottom-right"
              >
                <Background color="#2d3748" gap={20} />
                <Controls />
                <MiniMap nodeColor="#4f8ef7" maskColor="#0f111788" />
              </ReactFlow>
            </div>

            {selectedNode && (
              <div className="detail-panel">
                <div className="detail-header">
                  <span className="detail-title">📦 {selectedNode}</span>
                  <button className="detail-close" onClick={() => setSelectedNode(null)}>✕</button>
                </div>
                <p className="detail-label">Imports ({nodeImports.length})</p>
                {nodeImports.length > 0 ? (
                  <ul className="import-list">
                    {nodeImports.map(({ target, type }) => (
                      <li key={target} className={type} onClick={() => setSelectedNode(target)}>
                        {target}
                        <span className="import-type-badge">{type}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="detail-empty">No internal imports.</p>
                )}
              </div>
            )}
          </div>

          {(summary || !summaryDone) && (
            <div className="summary-box">
              <h2>🧠 Architectural Summary</h2>
              <p>
                {summary}
                {!summaryDone && <span className="cursor-blink">▌</span>}
              </p>
            </div>
          )}
        </div>
      )}

      {jobStatus === 'COMPLETED' && graphData?.nodes.length === 0 && (
        <p className="empty-note">No Python files with import relationships found.</p>
      )}
    </div>
  )
}