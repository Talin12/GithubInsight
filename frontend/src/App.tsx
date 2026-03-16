// frontend/src/App.tsx

import { useState, useEffect, useRef, useCallback } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
} from 'reactflow'
import 'reactflow/dist/style.css'
import './App.css'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED'

interface GraphData {
  nodes: { id: string; label: string }[]
  edges: { source: string; target: string }[]
}

interface JobResponse {
  job_id: string
  status: JobStatus
  repo_url: string
  graph_data?: GraphData
  error_message?: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 3000

function toReactFlowNodes(nodes: GraphData['nodes']): Node[] {
  return nodes.map((n, i) => ({
    id: n.id,
    data: { label: n.label },
    position: {
      x: (i % 6) * 220,
      y: Math.floor(i / 6) * 100,
    },
    style: {
      background: '#1a202c',
      border: '1px solid #4f8ef7',
      color: '#e2e8f0',
      borderRadius: 6,
      fontSize: 11,
      padding: '6px 10px',
    },
  }))
}

function toReactFlowEdges(edges: GraphData['edges']): Edge[] {
  return edges.map((e, i) => ({
    id: `e-${i}`,
    source: e.source,
    target: e.target,
    style: { stroke: '#4f8ef7', strokeWidth: 1.5 },
  }))
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function App() {
  const [repoUrl, setRepoUrl]       = useState('')
  const [jobId, setJobId]           = useState<string | null>(null)
  const [jobStatus, setJobStatus]   = useState<JobStatus | null>(null)
  const [graphData, setGraphData]   = useState<GraphData | null>(null)
  const [errorMsg, setErrorMsg]     = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
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
      setErrorMsg('Network error while polling job status.')
      stopPolling()
    }
  }, [stopPolling])

  useEffect(() => {
    if (!jobId) return
    pollRef.current = setInterval(() => pollJob(jobId), POLL_INTERVAL_MS)
    // Kick off immediately too.
    pollJob(jobId)
    return stopPolling
  }, [jobId, pollJob, stopPolling])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!repoUrl.trim()) return

    // Reset state.
    setJobId(null)
    setJobStatus(null)
    setGraphData(null)
    setErrorMsg(null)
    setSubmitting(true)

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

      const data = await res.json()
      setJobId(data.job_id)
      setJobStatus('PENDING')
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Submission failed.')
    } finally {
      setSubmitting(false)
    }
  }

  const rfNodes = graphData ? toReactFlowNodes(graphData.nodes) : []
  const rfEdges = graphData ? toReactFlowEdges(graphData.edges) : []

  return (
    <div className="app">
      <h1>🔍 RepoInsight</h1>

      <form className="form" onSubmit={handleSubmit}>
        <input
          type="url"
          placeholder="https://github.com/owner/repo"
          value={repoUrl}
          onChange={e => setRepoUrl(e.target.value)}
          disabled={submitting || jobStatus === 'PENDING' || jobStatus === 'PROCESSING'}
        />
        <button
          type="submit"
          disabled={submitting || jobStatus === 'PENDING' || jobStatus === 'PROCESSING'}
        >
          {submitting ? 'Submitting…' : 'Analyse'}
        </button>
      </form>

      {jobStatus && (
        <span className={`status-badge ${jobStatus}`}>
          {jobStatus === 'PENDING'    && '⏳ Pending'}
          {jobStatus === 'PROCESSING' && '⚙️ Processing'}
          {jobStatus === 'COMPLETED'  && '✅ Completed'}
          {jobStatus === 'FAILED'     && '❌ Failed'}
        </span>
      )}

      {errorMsg && (
        <div className="error-box">
          <strong>Error:</strong> {errorMsg}
        </div>
      )}

      {jobStatus === 'COMPLETED' && graphData && (
        <div className="graph-container">
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            fitView
            attributionPosition="bottom-right"
          >
            <Background color="#2d3748" gap={20} />
            <Controls />
            <MiniMap nodeColor="#4f8ef7" maskColor="#0f111788" />
          </ReactFlow>
        </div>
      )}

      {jobStatus === 'COMPLETED' && graphData?.nodes.length === 0 && (
        <p style={{ color: '#a0aec0', marginTop: '1rem' }}>
          No Python files with import relationships found in this repository.
        </p>
      )}
    </div>
  )
}