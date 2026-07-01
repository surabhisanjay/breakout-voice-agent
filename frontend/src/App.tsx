import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  AreaChart, Area, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell
} from 'recharts';
import {
  LayoutDashboard, Activity, Phone, Users, UserCheck, Calendar, GitMerge,
  AlertTriangle, UserCog, Users2, BarChart2, FileText, Settings, Search,
  Bell, Download, ChevronRight, ChevronLeft, RefreshCw, Plus, MoreHorizontal,
  TrendingUp, TrendingDown, DollarSign, Target, Clock, Star, Zap,
  MapPin, BookOpen, Mail, PhoneCall, PhoneOff, Eye, Edit2, Copy,
  List, Grid, X, Check, CheckCircle, XCircle, Shield, Play, HelpCircle
} from 'lucide-react';

/* ─── API CONFIG ─────────────────────────────────────────────────────────── */
const _host = window.location.host;
const _proto = window.location.protocol;
const _wsProto = _proto === 'https:' ? 'wss:' : 'ws:';

// Route local dev server ports (e.g. 5173, 3000) to the backend on port 8010
const isDev = _host.includes(':') && !_host.includes(':8010');
const apiHost = isDev ? `${window.location.hostname}:8010` : _host;
const apiProto = isDev ? 'http:' : _proto;
const wsProto = isDev ? 'ws:' : _wsProto;

const API_BASE = `${apiProto}//${apiHost}/api/analytics`;
const WS_ENDPOINT = `${wsProto}//${apiHost}/ws`;
const VAPI_BASE = `${apiProto}//${apiHost}`;

const PIE_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6'];

/* ─── HELPERS ────────────────────────────────────────────────────────────── */
const fmt = {
  currency: (v: number | null | undefined) => {
    if (v === null || v === undefined) return '—';
    return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v);
  },
  number: (v: number | null | undefined) => {
    if (v === null || v === undefined) return '—';
    return new Intl.NumberFormat('en-IN').format(v);
  },
  pct: (v: number | null | undefined) => {
    if (v === null || v === undefined) return '—';
    return `${v.toFixed(1)}%`;
  },
  duration: (s: number | null | undefined) => {
    if (!s) return '0s';
    const m = Math.floor(s / 60), sec = s % 60;
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  },
  date: (iso: string | null | undefined) => {
    if (!iso) return '—';
    return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  },
  relTime: (iso: string | null | undefined) => {
    if (!iso) return '—';
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
    return `${Math.floor(mins / 1440)}d ago`;
  }
};

/* ─── UI ELEMENTS ────────────────────────────────────────────────────────── */
function Avatar({ name, size = 32 }: { name: string; size?: number }) {
  const initials = name?.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2) || '??';
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%', background: '#EFF6FF',
      color: '#1D4ED8', display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: size * 0.38, fontWeight: 600, flexShrink: 0, border: '1px solid #BFDBFE'
    }}>
      {initials}
    </div>
  );
}

function Badge({ label, color = 'gray' }: { label: string; color?: 'blue' | 'green' | 'amber' | 'red' | 'purple' | 'gray' }) {
  const styles = {
    blue: { bg: '#EFF6FF', text: '#1D4ED8', border: '#BFDBFE' },
    green: { bg: '#F0FDF4', text: '#15803D', border: '#BBF7D0' },
    amber: { bg: '#FFFBEB', text: '#92400E', border: '#FDE68A' },
    red: { bg: '#FEF2F2', text: '#B91C1C', border: '#FECACA' },
    purple: { bg: '#FAF5FF', text: '#7E22CE', border: '#E9D5FF' },
    gray: { bg: '#F8FAFC', text: '#475569', border: '#E2E8F0' }
  }[color];
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', padding: '2px 8px', borderRadius: 4,
      background: styles.bg, color: styles.text, fontSize: 11, fontWeight: 600,
      border: `1px solid ${styles.border}`, whiteSpace: 'nowrap'
    }}>{label}</span>
  );
}

function KpiCard({ label, value, sub, icon: Icon, color = '#2563EB', trend }: {
  label: string; value: string | number; sub?: string; icon: any; color?: string; trend?: number;
}) {
  return (
    <div style={{
      background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, padding: 14,
      boxShadow: '0 1px 2px rgba(0,0,0,0.01)'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.02em' }}>{label}</span>
        <span style={{ width: 24, height: 24, borderRadius: 6, background: `${color}0D`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon size={12} color={color} />
        </span>
      </div>
      <div style={{ fontSize: 18, fontWeight: 700, color: '#0F172A', lineHeight: 1.2 }}>{value}</div>
      {(sub || trend !== undefined) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
          {trend !== undefined && (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, fontSize: 10, fontWeight: 600, color: trend >= 0 ? '#16A34A' : '#DC2626' }}>
              {trend >= 0 ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
              {Math.abs(trend).toFixed(1)}%
            </span>
          )}
          {sub && <span style={{ fontSize: 10, color: '#94A3B8' }}>{sub}</span>}
        </div>
      )}
    </div>
  );
}

function EmptyState({ icon: Icon, title, desc }: { icon: any; title: string; desc: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '40px 20px', textAlign: 'center' }}>
      <div style={{ width: 44, height: 44, borderRadius: '50%', background: '#F1F5F9', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 12 }}>
        <Icon size={20} color="#94A3B8" />
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#334155', marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 11, color: '#94A3B8', maxWidth: 260 }}>{desc}</div>
    </div>
  );
}

function Btn({ children, variant = 'secondary', size = 'sm', icon: Icon, onClick, style }: {
  children?: any; variant?: 'primary' | 'secondary' | 'danger' | 'ghost'; size?: 'xs' | 'sm' | 'md'; icon?: any; onClick?: () => void; style?: React.CSSProperties;
}) {
  const themeStyles = {
    primary: { background: '#2563EB', color: '#fff', border: '1px solid #2563EB' },
    secondary: { background: '#fff', color: '#334155', border: '1px solid #E2E8F0' },
    danger: { background: '#FEF2F2', color: '#DC2626', border: '1px solid #FEE2E2' },
    ghost: { background: 'transparent', color: '#475569', border: '1px solid transparent' }
  }[variant];
  const pad = { xs: '3px 8px', sm: '5px 12px', md: '7px 16px' }[size];
  const fs = { xs: 11, sm: 12, md: 13 }[size];
  return (
    <button onClick={onClick} style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, padding: pad, fontSize: fs,
      fontWeight: 500, borderRadius: 6, cursor: 'pointer', fontFamily: 'inherit',
      transition: 'all 0.1s', ...themeStyles, ...style
    }}>
      {Icon && <Icon size={fs + 1} />}
      {children}
    </button>
  );
}

/* ─── SIDEBAR ────────────────────────────────────────────────────────────── */
type Tab = 'dashboard' | 'live' | 'calls' | 'contacts' | 'leads' | 'bookings' | 'pipeline' | 'escalations' | 'agents' | 'teams' | 'analytics' | 'reports' | 'kb' | 'settings';

function Sidebar({ activeTab, setActiveTab, liveCount }: { activeTab: Tab; setActiveTab: (t: Tab) => void; liveCount: number }) {
  const menu = [
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'live', label: 'Live Calls', icon: Activity, badge: liveCount },
    { id: 'calls', label: 'Calls', icon: Phone },
    { id: 'contacts', label: 'Contacts', icon: Users },
    { id: 'leads', label: 'Leads', icon: UserCheck },
    { id: 'bookings', label: 'Bookings', icon: Calendar },
    { id: 'pipeline', label: 'Pipeline', icon: GitMerge },
    { id: 'escalations', label: 'Escalations', icon: AlertTriangle },
    { id: 'agents', label: 'Agents', icon: UserCog },
    { id: 'teams', label: 'Teams', icon: Users2 },
    { id: 'analytics', label: 'Analytics', icon: BarChart2 },
    { id: 'reports', label: 'Reports', icon: FileText },
    { id: 'kb', label: 'Knowledge Base', icon: BookOpen },
    { id: 'settings', label: 'Settings', icon: Settings },
  ];

  return (
    <aside style={{ width: 220, background: '#fff', borderRight: '1px solid #E2E8F0', display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
      <div style={{ height: 52, display: 'flex', alignItems: 'center', padding: '0 16px', borderBottom: '1px solid #E2E8F0', gap: 10 }}>
        <div style={{ width: 26, height: 26, borderRadius: 6, background: '#2563EB', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <PhoneCall size={13} color="#fff" />
        </div>
        <span style={{ fontWeight: 700, fontSize: 14, color: '#0F172A' }}>Closira Sales OS</span>
      </div>
      <nav style={{ flex: 1, padding: 8, display: 'flex', flexDirection: 'column', gap: 2, overflowY: 'auto' }}>
        {menu.map(item => {
          const active = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id as Tab)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px', borderRadius: 6,
                border: 'none', background: active ? '#EFF6FF' : 'transparent',
                color: active ? '#1D4ED8' : '#475569', fontSize: 13, fontWeight: active ? 600 : 500,
                textAlign: 'left', cursor: 'pointer', fontFamily: 'inherit', width: '100%'
              }}
            >
              <item.icon size={15} style={{ flexShrink: 0 }} />
              <span style={{ flex: 1 }}>{item.label}</span>
              {item.badge !== undefined && item.badge > 0 && (
                <span style={{
                  fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 10,
                  background: '#EF4444', color: '#fff'
                }}>{item.badge}</span>
              )}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}

/* ─── ROOT APP ────────────────────────────────────────────────────────────── */
export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard');
  const [searchQuery, setSearchQuery] = useState('');
  const [dateFilter, setDateFilter] = useState('30d');
  const [liveCalls, setLiveCalls] = useState<any[]>([]);
  const [allCalls, setAllCalls] = useState<any[]>([]);
  const [leads, setLeads] = useState<any[]>([]);
  const [agents, setAgents] = useState<any[]>([]);
  const [fullAnalytics, setFullAnalytics] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState('');
  const [isConnected, setIsConnected] = useState(false);

  // Detail/drawer views (Conversation Workspace)
  const [selectedCallId, setSelectedCallId] = useState<string | null>(null);
  const [selectedCallDetails, setSelectedCallDetails] = useState<any>(null);
  const [selectedCallTranscripts, setSelectedCallTranscripts] = useState<any[]>([]);
  const [selectedCallTasks, setSelectedCallTasks] = useState<any[]>([]);
  const [selectedCallNotes, setSelectedCallNotes] = useState<any[]>([]);
  const [selectedCallActivities, setSelectedCallActivities] = useState<any[]>([]);
  const [liveSentimentPoints, setLiveSentimentPoints] = useState<{ name: string; score: number; label: string }[]>([]);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  const [selectedLeadDetails, setSelectedLeadDetails] = useState<any>(null);
  const [isLeadDetailOpen, setIsLeadDetailOpen] = useState(false);

  const [notifs, setNotifs] = useState<any[]>([]);
  const [notifCount, setNotifCount] = useState(0);

  // Global search match logic
  const searchResults = useMemo(() => {
    if (!searchQuery) return null;
    const query = searchQuery.toLowerCase();
    return {
      contacts: leads.filter(l => `${l.first_name} ${l.last_name}`.toLowerCase().includes(query) || (l.email || '').toLowerCase().includes(query)),
      calls: allCalls.filter(c => c.session_id.toLowerCase().includes(query)),
      leads: leads.filter(l => (l.company_name || '').toLowerCase().includes(query) || (l.escape_room || '').toLowerCase().includes(query))
    };
  }, [searchQuery, leads, allCalls]);

  const refreshLiveCalls = useCallback(async () => {
    try {
      const liveRes = await fetch(`${VAPI_BASE}/vapi/calls/live`);
      if (!liveRes.ok) {
        throw new Error('Live call feed unavailable.');
      }
      const liveData = await liveRes.json();
      setLiveCalls(Array.isArray(liveData.calls) ? liveData.calls : []);
      return true;
    } catch (e) {
      console.warn('Live call refresh failed:', e);
      return false;
    }
  }, []);

  const fetchCRMState = useCallback(async () => {
    try {
      const [fullRes, sessionsRes, leadsRes, agentsRes] = await Promise.all([
        fetch(`${API_BASE}/full`),
        fetch(`${API_BASE}/sessions`),
        fetch(`${API_BASE}/leads`),
        fetch(`${API_BASE}/agents/list`)
      ]);

      const fullData = await fullRes.json();
      const sessionsData = await sessionsRes.json();
      const leadsData = await leadsRes.json();
      const agentsData = await agentsRes.json();

      setFullAnalytics(fullData);
      setAllCalls(Array.isArray(sessionsData) ? sessionsData : []);
      setLeads(Array.isArray(leadsData) ? leadsData : []);
      setAgents(Array.isArray(agentsData) ? agentsData : []);

      const liveOk = await refreshLiveCalls();
      if (!fullRes.ok || !sessionsRes.ok || !leadsRes.ok || !agentsRes.ok) {
        throw new Error('CRM data is partially unavailable. Live calls are still refreshing.');
      }
      if (!liveOk) {
        setErrorMsg('Live calls are temporarily unavailable. Check the backend websocket and /vapi/calls/live endpoint.');
      } else {
        setErrorMsg('');
      }
    } catch (e: any) {
      console.error(e);
      setErrorMsg(e.message || 'Error occurred connecting to Closira backend services.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleSelectCall = async (callId: string) => {
    try {
      setSelectedCallId(callId);
      setLiveSentimentPoints([]); // reset live points for new session
      const detailRes = await fetch(`${API_BASE}/sessions/${callId}`);
      if (!detailRes.ok) return;
      setSelectedCallDetails(await detailRes.json());

      // Resiliently fetch transcripts
      try {
        const transRes = await fetch(`${API_BASE}/sessions/${callId}/transcripts`);
        setSelectedCallTranscripts(transRes.ok ? await transRes.json() : []);
      } catch {
        setSelectedCallTranscripts([]);
      }

      // Resiliently fetch tasks
      try {
        const tasksRes = await fetch(`${API_BASE}/sessions/${callId}/tasks`);
        setSelectedCallTasks(tasksRes.ok ? await tasksRes.json() : []);
      } catch {
        setSelectedCallTasks([]);
      }

      // Resiliently fetch notes
      try {
        const notesRes = await fetch(`${API_BASE}/sessions/${callId}/notes`);
        setSelectedCallNotes(notesRes.ok ? await notesRes.json() : []);
      } catch {
        setSelectedCallNotes([]);
      }

      // Resiliently fetch activities
      try {
        const actRes = await fetch(`${API_BASE}/sessions/${callId}/activities`);
        setSelectedCallActivities(actRes.ok ? await actRes.json() : []);
      } catch {
        setSelectedCallActivities([]);
      }

      setIsDrawerOpen(true);
    } catch (err) {
      console.error('Error fetching call details:', err);
    }
  };

  const handleManualEscalate = async (callId: string) => {
    try {
      const res = await fetch(`${API_BASE}/sessions/${callId}/escalate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Manual Representative Override' })
      });
      if (res.ok) {
        handleSelectCall(callId);
        fetchCRMState();
      }
    } catch (err) {
      console.error('Failed to escalate call manually:', err);
    }
  };

  const handleSelectLead = (lead: any) => {
    setSelectedLeadDetails(lead);
    setIsLeadDetailOpen(true);
  };

  const handleStageChange = async (leadId: string, newStage: string) => {
    const lead = leads.find(l => l.lead_id === leadId);
    if (!lead) return;
    const updated = { ...lead, stage: newStage };
    try {
      const res = await fetch(`${API_BASE}/leads`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updated)
      });
      if (res.ok) {
        fetchCRMState();
      }
    } catch (err) {
      console.error('Failed to patch stage', err);
    }
  };

  const selectedCallIdRef = useRef<string | null>(null);
  useEffect(() => {
    selectedCallIdRef.current = selectedCallId;
  }, [selectedCallId]);

  // Real-time Event Listener (WebSocket /ws on port 8010)
  useEffect(() => {
    let ws: WebSocket;
    let reconnectTimer: any;

    const connectWS = () => {
      ws = new WebSocket(WS_ENDPOINT);

      ws.onopen = () => {
        setIsConnected(true);
        console.log('Voice CRM realtime connected.');
      };

      ws.onmessage = (event) => {
        try {
          const envelope = JSON.parse(event.data);
          const eventType = envelope.event_type || envelope.event;
          const payload = envelope.payload || envelope;

          if (eventType === 'call_started') {
            setNotifs(prev => [{ id: Date.now(), title: 'Call Started', text: `Session ${payload.session_id} is active.` }, ...prev]);
            setNotifCount(c => c + 1);
            refreshLiveCalls();
          } else if (eventType === 'escalated') {
            setNotifs(prev => [{ id: Date.now(), title: 'Escalation Triggered', text: `Loop/frustration detected on session ${payload.session_id}.` }, ...prev]);
            setNotifCount(c => c + 1);
          } else if (eventType === 'transcript_update') {
            if (selectedCallIdRef.current && payload.session_id === selectedCallIdRef.current) {
              const speaker = payload.speaker === 'user' || payload.speaker === 'Customer' ? 'Customer' : 'Agent';
              setSelectedCallTranscripts(prev => [...prev, { speaker, text: payload.text, timestamp: new Date().toLocaleTimeString() }]);
            }
          } else if (eventType === 'sentiment_update') {
            if (selectedCallIdRef.current && payload.session_id === selectedCallIdRef.current) {
              setLiveSentimentPoints(prev => [...prev, {
                name: `Turn ${prev.length + 1}`,
                score: payload.score ?? 0,
                label: payload.sentiment ?? 'neutral'
              }]);
            }
          } else if (eventType === 'call_updated' || eventType === 'call_ended') {
            refreshLiveCalls();
          }

          fetchCRMState();

          if (selectedCallIdRef.current && payload.session_id === selectedCallIdRef.current && eventType !== 'transcript_update') {
            handleSelectCall(payload.session_id);
          }
        } catch (err) {
          console.error('WebSocket parse error:', err);
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        reconnectTimer = setTimeout(connectWS, 5000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connectWS();
    return () => {
      ws?.close();
      clearTimeout(reconnectTimer);
    };
  }, [fetchCRMState]);

  useEffect(() => {
    fetchCRMState();
  }, [fetchCRMState]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      refreshLiveCalls();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshLiveCalls]);

  // Aggregate statistics for dashboard metrics
  const dashboardKpis = useMemo(() => {
    if (!fullAnalytics) return null;
    const kpis = fullAnalytics.kpis || {};
    const csat = fullAnalytics.csat || {};
    const llm = fullAnalytics.llm || {};
    const bookings = fullAnalytics.bookings || {};
    const ops = fullAnalytics.operational || {};

    return {
      activeCalls: liveCalls.length,
      bookingsToday: bookings.bookings || 0,
      pendingBookings: bookings.pending || 0,
      confirmedBookings: bookings.completed || 0,
      pipelineValue: kpis.pipeline_value || 0,
      conversion: fullAnalytics.funnel?.stages?.[4]?.conversion_rate || 0,
      csat: csat.csat_score || null,
      callDuration: 165.2,
      latency: llm.avg_latency_seconds || null,
      resolutionTime: fullAnalytics.workflow?.avg_automation_time_seconds || null,
      escalationRate: 5.4,
      handoffRate: 21.5,
      resolutionRate: 78.5,
      retrievalSuccess: fullAnalytics.knowledge?.retrieval_success_rate || 100.0,
      totalTokens: llm.total_tokens || 0,
      cost: llm.avg_cost_per_turn ? llm.avg_cost_per_turn * 100 : 0.45,
      successRate: 93.8,
      toolSuccess: 99.5,
      confidence: 85.4,
      activeAgents: ops.available_agents || 5,
      utilization: 82.5
    };
  }, [fullAnalytics, liveCalls, allCalls]);

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw', background: '#F8FAFC', color: '#0F172A', fontFamily: '"Inter", sans-serif', overflow: 'hidden' }}>
      {/* Sidebar Navigation */}
      <Sidebar activeTab={activeTab} setActiveTab={(t) => { setActiveTab(t); setIsDrawerOpen(false); setIsLeadDetailOpen(false); }} liveCount={liveCalls.length} />

      {/* Main Container */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Topbar */}
        <header style={{ height: 52, background: '#fff', borderBottom: '1px solid #E2E8F0', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 20px', zIndex: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <h2 style={{ fontSize: 15, fontWeight: 700, textTransform: 'capitalize' }}>{activeTab}</h2>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: isConnected ? '#16A34A' : '#94A3B8', fontWeight: 600 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: isConnected ? '#16A34A' : '#94A3B8', animation: isConnected ? 'pulse 2.3s infinite' : 'none' }} />
              {isConnected ? 'Real-time Linked' : 'Offline Mode'}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <SearchInput value={searchQuery} onChange={setSearchQuery} placeholder="Search CRM records..." />
            <Select value={dateFilter} onChange={setDateFilter} options={[
              { value: '7d', label: 'Last 7 Days' },
              { value: '30d', label: 'Last 30 Days' },
              { value: '90d', label: 'Last 90 Days' }
            ]} />
            <Btn icon={Download} variant="secondary" onClick={fetchCRMState}>Sync State</Btn>
            <div style={{ position: 'relative' }}>
              <Btn icon={Bell} variant="ghost" onClick={() => { setNotifCount(0); }} />
              {notifCount > 0 && (
                <span style={{ position: 'absolute', top: -3, right: -3, width: 14, height: 14, borderRadius: '50%', background: '#EF4444', color: '#fff', fontSize: 9, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700 }}>
                  {notifCount}
                </span>
              )}
            </div>
            <Avatar name="Closira Executive" size={28} />
          </div>
        </header>

        {/* Dynamic Global Search Overlay */}
        {searchResults && (
          <div style={{ position: 'absolute', top: 52, right: 200, width: 380, maxHeight: 400, background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.1)', zIndex: 100, overflowY: 'auto', padding: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, borderBottom: '1px solid #F1F5F9', paddingBottom: 6 }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: '#64748B' }}>Search Results</span>
              <Btn variant="ghost" size="xs" icon={X} onClick={() => setSearchQuery('')} />
            </div>
            {searchResults.contacts.length === 0 && searchResults.calls.length === 0 && searchResults.leads.length === 0 ? (
              <div style={{ padding: 12, textAlign: 'center', fontSize: 12, color: '#94A3B8' }}>No matches found</div>
            ) : (
              <div>
                {searchResults.contacts.length > 0 && (
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', marginBottom: 4 }}>Contacts</div>
                    {searchResults.contacts.map(c => (
                      <div key={c.lead_id} onClick={() => { handleSelectLead(c); setSearchQuery(''); }} style={{ padding: '6px 8px', borderRadius: 4, cursor: 'pointer' }}>
                        <div style={{ fontSize: 12, fontWeight: 600 }}>{c.first_name} {c.last_name}</div>
                        <div style={{ fontSize: 10, color: '#64748B' }}>{c.email}</div>
                      </div>
                    ))}
                  </div>
                )}
                {searchResults.calls.length > 0 && (
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', marginBottom: 4 }}>Calls</div>
                    {searchResults.calls.map(c => (
                      <div key={c.session_id} onClick={() => { handleSelectCall(c.session_id); setSearchQuery(''); }} style={{ padding: '6px 8px', borderRadius: 4, cursor: 'pointer' }}>
                        <div style={{ fontSize: 11, fontFamily: 'monospace', fontWeight: 600 }}>{c.session_id}</div>
                        <div style={{ fontSize: 10, color: '#64748B' }}>{c.agent_id}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Content Body */}
        <div style={{ flex: 1, overflowY: 'auto', background: '#F8FAFC', position: 'relative' }}>
          {errorMsg && (
            <div style={{ margin: 20, padding: '12px 16px', background: '#FEF2F2', border: '1px solid #FEE2E2', borderRadius: 6, display: 'flex', alignItems: 'center', gap: 10, color: '#B91C1C', fontSize: 13 }}>
              <AlertTriangle size={16} />
              <span>{errorMsg}</span>
            </div>
          )}

          {isLoading ? (
            <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 12 }}>
              <div style={{ width: 32, height: 32, borderRadius: '50%', border: '3px solid #E2E8F0', borderTopColor: '#2563EB', animation: 'spin 0.8s linear infinite' }} />
              <span style={{ fontSize: 13, color: '#64748B', fontWeight: 500 }}>Syncing Closira CRM state...</span>
            </div>
          ) : (
            <>
              {activeTab === 'dashboard' && <DashboardView kpis={dashboardKpis} data={fullAnalytics} leads={leads} />}
              {activeTab === 'live' && <LiveCallsView calls={liveCalls} onSelectCall={handleSelectCall} />}
              {activeTab === 'calls' && <CallsView calls={allCalls} onSelectCall={handleSelectCall} />}
              {activeTab === 'contacts' && <ContactsView leads={leads} onSelectContact={(lead: any) => handleSelectCall(lead.lead_id.replace('lead_20', 'sess_10'))} />}
              {activeTab === 'leads' && <LeadsView leads={leads} onSelectContact={(lead: any) => handleSelectCall(lead.lead_id.replace('lead_20', 'sess_10'))} onStageChange={handleStageChange} />}
              {activeTab === 'bookings' && <BookingsView data={fullAnalytics} leads={leads} onSelectContact={(lead: any) => handleSelectCall(lead.lead_id.replace('lead_20', 'sess_10'))} />}
              {activeTab === 'pipeline' && <PipelineView data={fullAnalytics} />}
              {activeTab === 'escalations' && <EscalationsView calls={allCalls} onSelectCall={handleSelectCall} />}
              {activeTab === 'agents' && <AgentsView data={fullAnalytics} agents={agents} />}
              {activeTab === 'teams' && <TeamsView data={fullAnalytics} />}
              {activeTab === 'analytics' && <AnalyticsView data={fullAnalytics} calls={allCalls} />}
              {activeTab === 'reports' && <ReportsView />}
              {activeTab === 'kb' && <KnowledgeBaseView data={fullAnalytics} />}
              {activeTab === 'settings' && <SettingsView />}
            </>
          )}
        </div>

        {/* Conversation Workspace (Live Call Drawer) */}
        {(() => {
          if (!isDrawerOpen || !selectedCallDetails) return null;
          
          const call = selectedCallDetails.call || {};
          const escalation = selectedCallDetails.escalation || {};
          const bookings = selectedCallDetails.bookings || [];
          const lead = leads.find(l => l.lead_id === call.session_id) || {};
          
          const facts = [
            `Customer: ${lead.first_name || 'Unknown'} ${lead.last_name || ''}`,
            `Phone: ${lead.phone || '8217008407'}`,
            lead.email ? `Email: ${lead.email}` : null,
            lead.group_size ? `Group size: ${lead.group_size} players` : null,
            lead.escape_room ? `Room Preference: ${lead.escape_room}` : null,
            lead.location ? `Location: ${lead.location}` : null,
            escalation.reason ? `Escalation Reason: ${escalation.reason}` : null,
            `Booking Status: ${bookings.length > 0 ? 'Confirmed' : 'Not started'}`
          ].filter(Boolean);
          
          const hSummary = facts.join('. ');
          
          // Merge DB historical sentiment with live WS points (deduplicated by turn index)
          const dbSentiment = (selectedCallDetails.sentiment_timeline || []).map((s: any, idx: number) => ({
            name: `Turn ${idx + 1}`,
            score: typeof s.score === 'number' ? s.score : 0,
            label: s.label || 'neutral'
          }));
          const mergedSentiment = dbSentiment.length > 0 ? dbSentiment : liveSentimentPoints;
          const sChartData = mergedSentiment.length > 0 ? mergedSentiment : [{ name: 'Start', score: 0, label: 'neutral' }];

          return (
            <div style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: 480, background: '#fff', borderLeft: '1px solid #E2E8F0', boxShadow: '-4px 0 24px rgba(0,0,0,0.08)', zIndex: 100, display: 'flex', flexDirection: 'column' }}>
              <div style={{ height: 52, padding: '0 16px', borderBottom: '1px solid #E2E8F0', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Activity size={15} color="#2563EB" />
                  <span style={{ fontSize: 14, fontWeight: 700 }}>Conversation Workspace</span>
                </div>
                <Btn variant="ghost" size="xs" icon={X} onClick={() => setIsDrawerOpen(false)} />
              </div>
              
              <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
                {/* Profile Card */}
                <div style={{ background: '#F8FAFC', border: '1px solid #E2E8F0', borderRadius: 8, padding: 12 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase' }}>Customer Profile</span>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 8, marginBottom: 10 }}>
                    <div>
                      <div style={{ fontSize: 9, color: '#64748B' }}>Call ID</div>
                      <div style={{ fontSize: 11, fontFamily: 'monospace', fontWeight: 600 }}>{selectedCallDetails.session_id}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 9, color: '#64748B' }}>Agent ID</div>
                      <div style={{ fontSize: 11, fontWeight: 600 }}>{selectedCallDetails.agent?.agent_id || 'inbound_agent'}</div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 10, borderTop: '1px solid #E2E8F0', paddingTop: 8 }}>
                    <Btn
                      variant="danger"
                      size="xs"
                      icon={AlertTriangle}
                      onClick={() => handleManualEscalate(selectedCallDetails.session_id)}
                      style={{ flex: 1, justifyContent: 'center' }}
                    >
                      Trigger Manual Escalation Override
                    </Btn>
                  </div>
                </div>

                {/* Handoff Summary */}
                <div style={{ background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: 8, padding: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                    <Shield size={12} color="#16A34A" />
                    <span style={{ fontSize: 11, fontWeight: 700, color: '#15803D', textTransform: 'uppercase' }}>AI Handoff Summary</span>
                  </div>
                  <div style={{ fontSize: 11, color: '#166534', lineHeight: 1.4 }}>
                    {hSummary || 'No customer qualification data gathered yet.'}
                  </div>
                </div>

                {/* Sentiment Trajectory Graph */}
                {/* Sentiment Trajectory Graph — always shown */}
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase' }}>Sentiment Trajectory</span>
                    <span style={{
                      fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 99,
                      background: sChartData[sChartData.length - 1]?.score > 0 ? '#DCFCE7' : sChartData[sChartData.length - 1]?.score < 0 ? '#FEE2E2' : '#F1F5F9',
                      color: sChartData[sChartData.length - 1]?.score > 0 ? '#16A34A' : sChartData[sChartData.length - 1]?.score < 0 ? '#DC2626' : '#64748B'
                    }}>
                      {sChartData[sChartData.length - 1]?.label || 'neutral'}
                    </span>
                  </div>
                  <div style={{ height: 100, border: '1px solid #E2E8F0', borderRadius: 8, padding: '8px 4px', background: '#FAFBFC', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                    {mergedSentiment.length <= 1 && liveSentimentPoints.length === 0 ? (
                      <span style={{ fontSize: 11, color: '#94A3B8' }}>Waiting for live sentiment data…</span>
                    ) : (
                      <LineChart width={410} height={80} data={sChartData} margin={{ top: 4, bottom: 4, left: 0, right: 0 }}>
                        <YAxis domain={[-1.5, 1.5]} hide />
                        <Tooltip formatter={(_v: any, _n: any, props: any) => [props.payload.label, 'Sentiment']} />
                        <Line type="monotone" dataKey="score" stroke="#2563EB" strokeWidth={2.5} dot={{ r: 4, fill: '#2563EB', strokeWidth: 0 }} activeDot={{ r: 5 }} />
                      </LineChart>
                    )}
                  </div>
                </div>

                {/* Streaming Transcript */}
                <div>
                  <span style={{ fontSize: 10, fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase' }}>Live Transcript</span>
                  <div style={{ border: '1px solid #E2E8F0', borderRadius: 8, padding: 12, maxHeight: 180, overflowY: 'auto', background: '#FFF', marginTop: 6 }}>
                    {selectedCallTranscripts.length === 0 ? (
                      <div style={{ padding: 12, textAlign: 'center', color: '#94A3B8', fontSize: 11 }}>No transcript logs available</div>
                    ) : (
                      selectedCallTranscripts.map((t, idx) => (
                        <div key={idx} style={{ marginBottom: 8 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                            <span style={{ fontSize: 11, fontWeight: 700, color: t.speaker === 'Customer' ? '#7C3AED' : '#1D4ED8' }}>{t.speaker}</span>
                            <span style={{ fontSize: 9, color: '#94A3B8' }}>{t.timestamp || 'Live'}</span>
                          </div>
                          <div style={{ fontSize: 12, color: '#334155', background: '#F8FAFC', padding: 6, borderRadius: 6 }}>{t.text}</div>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                {/* LangGraph Node / State */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <div style={{ background: '#F8FAFC', border: '1px solid #E2E8F0', borderRadius: 8, padding: 10 }}>
                    <span style={{ fontSize: 9, color: '#64748B', display: 'block' }}>Current Node</span>
                    <Badge label="discovery" color="blue" />
                  </div>
                  <div style={{ background: '#F8FAFC', border: '1px solid #E2E8F0', borderRadius: 8, padding: 10 }}>
                    <span style={{ fontSize: 9, color: '#64748B', display: 'block' }}>Intent</span>
                    <Badge label={selectedCallDetails.call?.intent || 'general_faq'} color="purple" />
                  </div>
                </div>

                {/* Tasks, Notes, Activities Lists */}
                {selectedCallTasks.length > 0 && (
                  <div>
                    <span style={{ fontSize: 10, fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase' }}>Suggested Actions</span>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
                      {selectedCallTasks.map((task, tIdx) => (
                        <div key={tIdx} style={{ fontSize: 11, padding: 8, background: '#EFF6FF', borderRadius: 6, borderLeft: '3px solid #2563EB' }}>
                          {task.description}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          );
        })()}

        {/* Lead Details Workspace */}
        {isLeadDetailOpen && selectedLeadDetails && (
          <div style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: 480, background: '#fff', borderLeft: '1px solid #E2E8F0', boxShadow: '-4px 0 24px rgba(0,0,0,0.08)', zIndex: 100, display: 'flex', flexDirection: 'column' }}>
            <div style={{ height: 52, padding: '0 16px', borderBottom: '1px solid #E2E8F0', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Users size={15} color="#2563EB" />
                <span style={{ fontSize: 14, fontWeight: 700 }}>Lead Workspace</span>
              </div>
              <Btn variant="ghost" size="xs" icon={X} onClick={() => setIsLeadDetailOpen(false)} />
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <Avatar name={`${selectedLeadDetails.first_name} ${selectedLeadDetails.last_name}`} size={40} />
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700 }}>{selectedLeadDetails.first_name} {selectedLeadDetails.last_name}</div>
                  <div style={{ fontSize: 11, color: '#64748B' }}>{selectedLeadDetails.company_name || 'Individual'}</div>
                </div>
              </div>

              {/* Quick Info Grid */}
              <div style={{ background: '#F8FAFC', border: '1px solid #E2E8F0', borderRadius: 8, padding: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <div>
                    <span style={{ fontSize: 9, color: '#64748B' }}>Email</span>
                    <div style={{ fontSize: 11, fontWeight: 600 }}>{selectedLeadDetails.email || '—'}</div>
                  </div>
                  <div>
                    <span style={{ fontSize: 9, color: '#64748B' }}>Phone</span>
                    <div style={{ fontSize: 11, fontWeight: 600 }}>{selectedLeadDetails.phone || '—'}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── DASHBOARD VIEW ─────────────────────────────────────────────────────── */
function DashboardView({ kpis, data, leads }: { kpis: any; data: any; leads: any[] }) {
  if (!kpis) return null;

  const revData = useMemo(() => {
    return (data?.kpis?.revenue_trend || [10, 20, 15, 30, 25, 35]).map((val: number, idx: number) => ({
      name: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'][idx] || 'Month',
      value: val
    }));
  }, [data]);

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 22 Executive KPIs Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
        <KpiCard label="Active AI Calls" value={kpis.activeCalls} icon={Activity} color="#16A34A" />
        <KpiCard label="Bookings Today" value={kpis.bookingsToday} icon={Calendar} color="#2563EB" />
        <KpiCard label="Pending Bookings" value={kpis.pendingBookings} icon={Clock} color="#F59E0B" />
        <KpiCard label="Confirmed Bookings" value={kpis.confirmedBookings} icon={CheckCircle} color="#10B981" />
        <KpiCard label="Pipeline Value" value={fmt.currency(kpis.pipelineValue)} icon={DollarSign} color="#8B5CF6" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
        <KpiCard label="Conversion Rate" value={fmt.pct(kpis.conversion)} icon={Target} color="#2563EB" />
        <KpiCard label="CSAT" value={kpis.csat ? `${kpis.csat}/5` : '—'} icon={Star} color="#F59E0B" />
        <KpiCard label="Avg Call Duration" value={fmt.duration(kpis.callDuration)} icon={Clock} color="#06B6D4" />
        <KpiCard label="Avg Response Time" value={kpis.latency ? `${kpis.latency}s` : '—'} icon={Clock} color="#0891B2" />
        <KpiCard label="Avg Resolution Time" value={kpis.resolutionTime ? `${kpis.resolutionTime}s` : '—'} icon={Clock} color="#0891B2" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
        <KpiCard label="Escalation Rate" value={fmt.pct(kpis.escalationRate)} icon={AlertTriangle} color="#EF4444" />
        <KpiCard label="Human Handoff Rate" value={fmt.pct(kpis.handoffRate)} icon={AlertTriangle} color="#EF4444" />
        <KpiCard label="AI Resolution Rate" value={fmt.pct(kpis.resolutionRate)} icon={CheckCircle} color="#16A34A" />
        <KpiCard label="RAG Success" value={fmt.pct(kpis.retrievalSuccess)} icon={BookOpen} color="#10B981" />
        <KpiCard label="LLM Latency" value={kpis.latency ? `${kpis.latency}s` : '—'} icon={Clock} color="#7C3AED" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
        <KpiCard label="Token Usage" value={fmt.number(kpis.totalTokens)} icon={Star} color="#8B5CF6" />
        <KpiCard label="Estimated AI Cost" value={fmt.currency(kpis.cost)} icon={DollarSign} color="#2563EB" />
        <KpiCard label="Conversation Success" value={fmt.pct(kpis.successRate)} icon={CheckCircle} color="#16A34A" />
        <KpiCard label="Tool Success Rate" value={fmt.pct(kpis.toolSuccess)} icon={Target} color="#10B981" />
        <KpiCard label="Avg Intent Confidence" value={fmt.pct(kpis.confidence)} icon={Star} color="#F59E0B" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
        <KpiCard label="Active Agents" value={kpis.activeAgents} icon={UserCog} color="#2563EB" />
        <KpiCard label="Agent Utilization" value={fmt.pct(kpis.utilization)} icon={Activity} color="#10B981" />
      </div>

      {/* Chart Row */}
      <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 16 }}>
        <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, padding: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#0F172A', marginBottom: 12 }}>Revenue Growth Trend</div>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={revData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#94A3B8' }} />
              <YAxis tick={{ fontSize: 11, fill: '#94A3B8' }} tickFormatter={v => `₹${v/1000}k`} />
              <Tooltip formatter={(v: any) => [`₹${v}`, 'Revenue']} />
              <Area type="monotone" dataKey="value" stroke="#2563EB" fill="#EFF6FF" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Funnel list */}
        <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, padding: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#0F172A', marginBottom: 12 }}>Lead Progression Pipeline</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(data?.funnel?.stages || []).map((stage: any, idx: number) => (
              <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 10px', background: '#F8FAFC', borderRadius: 6 }}>
                <span style={{ fontSize: 12, fontWeight: 600 }}>{stage.stage}</span>
                <div style={{ display: 'flex', gap: 8, fontSize: 11 }}>
                  <span style={{ color: '#2563EB', fontWeight: 700 }}>{stage.count} leads</span>
                  <span style={{ color: '#94A3B8' }}>{stage.conversion_rate.toFixed(0)}% conv</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── LIVE CALLS VIEW ────────────────────────────────────────────────────── */
function LiveCallsView({ calls, onSelectCall }: { calls: any[]; onSelectCall: (id: string) => void }) {
  return (
    <div style={{ padding: 20 }}>
      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: 16, borderBottom: '1px solid #E2E8F0' }}>
          <h3 style={{ fontSize: 14, fontWeight: 700 }}>Active Voice Agent Operations Queue</h3>
        </div>
        {calls.length === 0 ? (
          <EmptyState icon={PhoneOff} title="No Active Live Calls" desc="No Vapi call sessions active right now." />
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#F8FAFC', borderBottom: '1px solid #E2E8F0', textAlign: 'left', fontSize: 11, color: '#64748B' }}>
                <th style={{ padding: 12 }}>Conversation ID</th>
                <th style={{ padding: 12 }}>Assistant</th>
                <th style={{ padding: 12 }}>Status</th>
                <th style={{ padding: 12 }}>Started At</th>
                <th style={{ padding: 12 }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {calls.map(call => (
                <tr key={call.call_id} style={{ borderBottom: '1px solid #F1F5F9', fontSize: 12 }}>
                  <td style={{ padding: 12, fontFamily: 'monospace' }}>{call.call_id}</td>
                  <td style={{ padding: 12 }}>{call.assistant_id || 'inbound_agent'}</td>
                  <td style={{ padding: 12 }}><Badge label={call.status || 'Active'} color="green" /></td>
                  <td style={{ padding: 12 }}>{fmt.relTime(call.started_at)}</td>
                  <td style={{ padding: 12 }}><Btn size="xs" onClick={() => onSelectCall(call.call_id)}>Open Console</Btn></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/* ─── CALLS VIEW ─────────────────────────────────────────────────────────── */
function CallsView({ calls, onSelectCall }: { calls: any[]; onSelectCall: (id: string) => void }) {
  return (
    <div style={{ padding: 20 }}>
      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: '#F8FAFC', borderBottom: '1px solid #E2E8F0', textAlign: 'left', fontSize: 11, color: '#64748B' }}>
              <th style={{ padding: 12 }}>Call ID</th>
              <th style={{ padding: 12 }}>Agent</th>
              <th style={{ padding: 12 }}>Status</th>
              <th style={{ padding: 12 }}>Duration</th>
              <th style={{ padding: 12 }}>Started At</th>
              <th style={{ padding: 12 }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {calls.map(c => (
              <tr key={c.session_id} style={{ borderBottom: '1px solid #F1F5F9', fontSize: 12 }}>
                <td style={{ padding: 12, fontFamily: 'monospace' }}>{c.session_id}</td>
                <td style={{ padding: 12 }}>{c.agent_id}</td>
                <td style={{ padding: 12 }}><Badge label={c.status} color={c.status === 'completed' ? 'green' : 'amber'} /></td>
                <td style={{ padding: 12 }}>{fmt.duration(c.duration)}</td>
                <td style={{ padding: 12 }}>{fmt.date(c.started_at)}</td>
                <td style={{ padding: 12 }}><Btn size="xs" onClick={() => onSelectCall(c.session_id)}>View Details</Btn></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── CONTACTS VIEW ──────────────────────────────────────────────────────── */
function ContactsView({ leads, onSelectContact }: { leads: any[]; onSelectContact: (l: any) => void }) {
  return (
    <div style={{ padding: 20 }}>
      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: '#F8FAFC', borderBottom: '1px solid #E2E8F0', textAlign: 'left', fontSize: 11, color: '#64748B' }}>
              <th style={{ padding: 12 }}>Customer</th>
              <th style={{ padding: 12 }}>Email</th>
              <th style={{ padding: 12 }}>Phone</th>
              <th style={{ padding: 12 }}>Company</th>
              <th style={{ padding: 12 }}>Stage</th>
              <th style={{ padding: 12 }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {leads.map(lead => (
              <tr key={lead.lead_id} style={{ borderBottom: '1px solid #F1F5F9', fontSize: 12 }}>
                <td style={{ padding: 12, fontWeight: 600 }}>{lead.first_name} {lead.last_name}</td>
                <td style={{ padding: 12 }}>{lead.email || '—'}</td>
                <td style={{ padding: 12 }}>{lead.phone || '—'}</td>
                <td style={{ padding: 12 }}>{lead.company_name || '—'}</td>
                <td style={{ padding: 12 }}><Badge label={lead.stage || 'New'} color="blue" /></td>
                <td style={{ padding: 12 }}><Btn size="xs" onClick={() => onSelectContact(lead)}>View Profile</Btn></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── LEADS VIEW ─────────────────────────────────────────────────────────── */
function LeadsView({ leads, onSelectContact, onStageChange }: { leads: any[]; onSelectContact: (l: any) => void; onStageChange: (id: string, stage: string) => void }) {
  const stages = ['New', 'Qualified', 'Interested', 'Booking', 'Payment', 'Won', 'Lost'];

  return (
    <div style={{ padding: 20, display: 'flex', gap: 12, overflowX: 'auto', height: 'calc(100vh - 120px)' }}>
      {stages.map(stage => {
        const stageLeads = leads.filter(l => (l.stage || 'New') === stage);
        return (
          <div key={stage} style={{ width: 250, background: '#F1F5F9', borderRadius: 8, display: 'flex', flexDirection: 'column', flexShrink: 0, padding: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, padding: '0 4px' }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: '#475569' }}>{stage}</span>
              <span style={{ fontSize: 10, background: '#E2E8F0', padding: '2px 6px', borderRadius: 10, fontWeight: 600 }}>{stageLeads.length}</span>
            </div>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto' }}>
              {stageLeads.map(l => (
                <div key={l.lead_id} style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 6, padding: 10, cursor: 'pointer', boxShadow: '0 1px 2px rgba(0,0,0,0.02)' }} onClick={() => onSelectContact(l)}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: '#0F172A', marginBottom: 4 }}>{l.first_name} {l.last_name}</div>
                  <div style={{ fontSize: 11, color: '#64748B', marginBottom: 6 }}>{l.escape_room || 'General Inquiry'}</div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: '#2563EB' }}>{fmt.currency(l.value)}</span>
                    <select
                      value={l.stage}
                      onClick={e => e.stopPropagation()}
                      onChange={e => onStageChange(l.lead_id, e.target.value)}
                      style={{ fontSize: 10, padding: '2px 4px', border: '1px solid #CBD5E1', borderRadius: 4, height: 20, minHeight: 20 }}
                    >
                      {stages.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ─── BOOKINGS VIEW ──────────────────────────────────────────────────────── */
function BookingsView({ data, leads, onSelectContact }: { data: any; leads: any[]; onSelectContact: (l: any) => void }) {
  const confirmedLeads = leads.filter(l => l.booking_date);
  
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      {data?.bookings && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
          <KpiCard label="Total Bookings" value={data.bookings.bookings} icon={Calendar} color="#2563EB" />
          <KpiCard label="Confirmed" value={data.bookings.completed} icon={CheckCircle} color="#16A34A" />
          <KpiCard label="Pending Payment" value={data.bookings.pending} icon={Clock} color="#F59E0B" />
          <KpiCard label="Cancelled" value={data.bookings.cancelled} icon={XCircle} color="#EF4444" />
        </div>
      )}

      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: '#F8FAFC', borderBottom: '1px solid #E2E8F0', textAlign: 'left', fontSize: 11, color: '#64748B' }}>
              <th style={{ padding: 12 }}>Booking ID</th>
              <th style={{ padding: 12 }}>Customer</th>
              <th style={{ padding: 12 }}>Room/Game</th>
              <th style={{ padding: 12 }}>Scheduled Date</th>
              <th style={{ padding: 12 }}>Time Slot</th>
              <th style={{ padding: 12 }}>Stage</th>
              <th style={{ padding: 12 }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {confirmedLeads.map(l => (
              <tr key={l.lead_id} style={{ borderBottom: '1px solid #F1F5F9', fontSize: 12 }}>
                <td style={{ padding: 12, fontFamily: 'monospace' }}>{l.booking_id || `bk_${l.lead_id.slice(-6)}`}</td>
                <td style={{ padding: 12, fontWeight: 600 }}>{l.first_name} {l.last_name}</td>
                <td style={{ padding: 12 }}>{l.escape_room}</td>
                <td style={{ padding: 12 }}>{l.booking_date}</td>
                <td style={{ padding: 12 }}>{l.booking_time}</td>
                <td style={{ padding: 12 }}><Badge label={l.stage} color="green" /></td>
                <td style={{ padding: 12 }}><Btn size="xs" onClick={() => onSelectContact(l)}>View Workspace</Btn></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── PIPELINE VIEW ──────────────────────────────────────────────────────── */
function PipelineView({ data }: { data: any }) {
  const steps = data?.funnel?.stages || [];

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, padding: 20 }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 16 }}>Funnel Stage Dropoffs</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {steps.map((s: any, idx: number) => (
            <div key={idx} style={{ position: 'relative', background: '#F8FAFC', border: '1px solid #E2E8F0', borderRadius: 6, padding: 12, display: 'flex', justifyContent: 'space-between', zIndex: 1 }}>
              <span style={{ fontSize: 12, fontWeight: 600 }}>{s.stage}</span>
              <div style={{ fontSize: 12, display: 'flex', gap: 12 }}>
                <span style={{ color: '#2563EB', fontWeight: 700 }}>{s.count} leads</span>
                <span style={{ color: '#64748B' }}>{s.conversion_rate.toFixed(1)}% conversion</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─── ESCALATIONS VIEW ───────────────────────────────────────────────────── */
function EscalationsView({ calls, onSelectCall }: { calls: any[]; onSelectCall: (id: string) => void }) {
  const escalations = calls.filter(c => c.status === 'escalated');

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: 16, borderBottom: '1px solid #E2E8F0' }}>
          <h3 style={{ fontSize: 13, fontWeight: 700, color: '#0F172A' }}>Active Support Tickets Queue</h3>
        </div>
        {escalations.length === 0 ? (
          <EmptyState icon={AlertTriangle} title="No Active Escalations" desc="No support escalations found." />
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#F8FAFC', borderBottom: '1px solid #E2E8F0', textAlign: 'left', fontSize: 11, color: '#64748B' }}>
                <th style={{ padding: 12 }}>Session ID</th>
                <th style={{ padding: 12 }}>Agent ID</th>
                <th style={{ padding: 12 }}>Status</th>
                <th style={{ padding: 12 }}>Reason</th>
                <th style={{ padding: 12 }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {escalations.map(esc => (
                <tr key={esc.session_id} style={{ borderBottom: '1px solid #F1F5F9', fontSize: 12 }}>
                  <td style={{ padding: 12, fontFamily: 'monospace' }}>{esc.session_id}</td>
                  <td style={{ padding: 12 }}>{esc.agent_id}</td>
                  <td style={{ padding: 12 }}><Badge label="Escalated" color="red" /></td>
                  <td style={{ padding: 12 }}>Loop / repetition triggered</td>
                  <td style={{ padding: 12 }}><Btn size="xs" onClick={() => onSelectCall(esc.session_id)}>View Workspace</Btn></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/* ─── AGENTS VIEW ────────────────────────────────────────────────────────── */
function AgentsView({ data, agents }: { data: any; agents: any[] }) {
  const leaderboard = data?.agents?.leaderboard || [];

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: 16, borderBottom: '1px solid #E2E8F0' }}>
          <h3 style={{ fontSize: 14, fontWeight: 700 }}>AI Reps Registry</h3>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: '#F8FAFC', borderBottom: '1px solid #E2E8F0', textAlign: 'left', fontSize: 11, color: '#64748B' }}>
              <th style={{ padding: 12 }}>Agent ID</th>
              <th style={{ padding: 12 }}>Calls</th>
              <th style={{ padding: 12 }}>Bookings</th>
              <th style={{ padding: 12 }}>Revenue</th>
              <th style={{ padding: 12 }}>Conversion</th>
            </tr>
          </thead>
          <tbody>
            {leaderboard.map((item: any, idx: number) => (
              <tr key={idx} style={{ borderBottom: '1px solid #F1F5F9', fontSize: 12 }}>
                <td style={{ padding: 12, fontWeight: 600 }}>{item.agent_id}</td>
                <td style={{ padding: 12 }}>{item.calls_made}</td>
                <td style={{ padding: 12 }}>{item.bookings}</td>
                <td style={{ padding: 12, fontWeight: 700 }}>{fmt.currency(item.revenue)}</td>
                <td style={{ padding: 12 }}>{fmt.pct(item.conversion_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── TEAMS VIEW ─────────────────────────────────────────────────────────── */
function TeamsView({ data }: { data: any }) {
  const teams = data?.teams?.teams || [];

  return (
    <div style={{ padding: 20 }}>
      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, padding: 20 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 12 }}>Team Performance Metrics</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {teams.map((t: any, idx: number) => (
            <div key={idx} style={{ background: '#F8FAFC', border: '1px solid #E2E8F0', borderRadius: 6, padding: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span style={{ fontSize: 13, fontWeight: 700 }}>{t.team_name}</span>
                <span style={{ fontSize: 13, color: '#2563EB', fontWeight: 700 }}>{fmt.currency(t.revenue)}</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, fontSize: 11, color: '#64748B' }}>
                <div>Bookings: <strong>{t.bookings}</strong></div>
                <div>Avg Latency: <strong>{t.avg_response_time}s</strong></div>
                <div>Quality Score: <strong>{t.avg_quality}/14</strong></div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─── ANALYTICS VIEW ─────────────────────────────────────────────────────── */
function AnalyticsView({ data, calls }: { data: any; calls: any[] }) {
  const llm = data?.llm || {};
  const ops = data?.operational || {};

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <KpiCard label="JSON Success Rate" value={llm.json_success_rate ? `${llm.json_success_rate}%` : '—'} icon={Star} color="#16A34A" />
        <KpiCard label="Function Success" value={llm.function_call_success_rate ? `${llm.function_call_success_rate}%` : '—'} icon={Target} color="#2563EB" />
        <KpiCard label="Total Tokens Used" value={fmt.number(llm.total_tokens)} icon={Star} color="#8B5CF6" />
        <KpiCard label="RAG Citation Accuracy" value={data?.knowledge?.citation_accuracy ? `${data.knowledge.citation_accuracy}%` : '—'} icon={BookOpen} color="#06B6D4" />
      </div>

      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, padding: 16 }}>
        <h3 style={{ fontSize: 13, fontWeight: 700, marginBottom: 12 }}>System health overview</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
          <div>
            <div style={{ fontSize: 11, color: '#64748B' }}>API Failures</div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>{ops.api_failures || 0}</div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: '#64748B' }}>Webhook Errors</div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>{ops.webhook_failures || 0}</div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: '#64748B' }}>Tool Failures</div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>{ops.tool_failures || 0}</div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: '#64748B' }}>LLM Inbound Errors</div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>{ops.llm_errors || 0}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── REPORTS VIEW ───────────────────────────────────────────────────────── */
function ReportsView() {
  return (
    <div style={{ padding: 20 }}>
      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, padding: 20 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>Export Operations Dashboard Reports</h3>
        <p style={{ fontSize: 12, color: '#64748B', marginBottom: 16 }}>Compile metrics and export CSV/PDF summaries directly from CRM SQLite.</p>
        <div style={{ display: 'flex', gap: 10 }}>
          <Btn variant="primary" icon={Download}>Export CRM Database (CSV)</Btn>
          <Btn variant="secondary" icon={FileText}>Generate Executive PDF Summary</Btn>
        </div>
      </div>
    </div>
  );
}

/* ─── KNOWLEDGE BASE VIEW ────────────────────────────────────────────────── */
function KnowledgeBaseView({ data }: { data: any }) {
  const kb = data?.knowledge || {};

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <KpiCard label="Retrieval Success" value={kb.retrieval_success_rate ? `${kb.retrieval_success_rate}%` : '—'} icon={BookOpen} color="#16A34A" />
        <KpiCard label="Retrieval Latency" value={kb.retrieval_latency_seconds ? `${kb.retrieval_latency_seconds}s` : '—'} icon={Clock} color="#2563EB" />
        <KpiCard label="Citation Accuracy" value={kb.citation_accuracy ? `${kb.citation_accuracy}%` : '—'} icon={Star} color="#8B5CF6" />
        <KpiCard label="FAQ Hit Rate" value={kb.faq_hit_rate ? `${kb.faq_hit_rate}%` : '—'} icon={Target} color="#06B6D4" />
      </div>

      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, padding: 20 }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12 }}>Indexed FAQ Documents</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: '#F8FAFC', borderBottom: '1px solid #E2E8F0', textAlign: 'left', fontSize: 11, color: '#64748B' }}>
              <th style={{ padding: 10 }}>Document Name</th>
              <th style={{ padding: 10 }}>Type</th>
              <th style={{ padding: 10 }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {[
              { name: 'safety_regulations.txt', type: 'Policy', status: 'Indexed' },
              { name: 'pricing_tier_2026.txt', type: 'Pricing', status: 'Indexed' },
              { name: 'booking_faq.txt', type: 'FAQ', status: 'Indexed' }
            ].map((doc, idx) => (
              <tr key={idx} style={{ borderBottom: '1px solid #F1F5F9', fontSize: 12 }}>
                <td style={{ padding: 10, fontWeight: 600 }}>{doc.name}</td>
                <td style={{ padding: 10 }}>{doc.type}</td>
                <td style={{ padding: 10 }}><Badge label={doc.status} color="green" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── SETTINGS VIEW ──────────────────────────────────────────────────────── */
function SettingsView() {
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8, padding: 20 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 12 }}>CRM Integrations & Safe outreach</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <span style={{ fontSize: 11, color: '#64748B', display: 'block', marginBottom: 4 }}>FastAPI Webhook Path</span>
            <code style={{ background: '#F1F5F9', padding: '4px 8px', borderRadius: 4, fontSize: 11 }}>/vapi/webhook</code>
          </div>
          <div>
            <span style={{ fontSize: 11, color: '#64748B', display: 'block', marginBottom: 4 }}>FastAPI Tool Request Path</span>
            <code style={{ background: '#F1F5F9', padding: '4px 8px', borderRadius: 4, fontSize: 11 }}>/vapi/tool</code>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── ATOMS ──────────────────────────────────────────────────────────────── */
function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: { label: string; value: string }[] }) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      style={{
        height: 30, padding: '0 24px 0 10px', fontSize: 12, fontWeight: 500,
        border: '1px solid #E2E8F0', borderRadius: 6, background: '#fff',
        color: '#334155', cursor: 'pointer', appearance: 'none',
        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2394A3B8' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")`,
        backgroundRepeat: 'no-repeat', backgroundPosition: 'right 8px center',
      }}
    >
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

function SearchInput({ value, onChange, placeholder = 'Search...' }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <div style={{ position: 'relative' }}>
      <Search size={13} style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', color: '#94A3B8' }} />
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          height: 30, padding: '0 10px 0 28px', fontSize: 12,
          border: '1px solid #E2E8F0', borderRadius: 6, width: 180,
          outline: 'none', background: '#fff', color: '#0F172A'
        }}
      />
    </div>
  );
}
