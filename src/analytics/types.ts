/**
 * TypeScript definitions for Voice CRM / Sales OS Analytics Layer.
 * Defines interfaces matching the 18 metrics categories and dashboard states.
 */

export interface GlobalFilters {
  startDate?: string;
  endDate?: string;
  agentId?: string;
  teamId?: string;
  campaign?: string;
  department?: string;
  location?: string;
  room?: string;
  bookingType?: string;
  leadSource?: string;
  sentiment?: string;
  outcome?: string;
  priority?: string;
  channel?: string;
  tags?: string[];
}

// 1. Executive KPIs
export interface ExecutiveKPIs {
  revenue: number;
  revenueTrend: number[];
  avgDealValue: number;
  revenuePerAgent: number;
  revenuePerTeam: number;
  revenuePerChannel: Record<string, number>;
  revenuePerCampaign: Record<string, number>;
  revenueGrowthPercent: number;
  bookings: number;
  payments: number;
  wonDeals: number;
  lostDeals: number;
  pipelineValue: number;
  weightedPipeline: number;
  forecastRevenue: number;
  mrr: number;
  arr: number;
}

// 2. Sales Funnel
export interface FunnelStage {
  stage: string;
  count: number;
  conversionRate: number;
  dropoffRate: number;
  avgTimeInStageMinutes: number;
  medianTimeMinutes: number;
}

export interface FunnelAnalytics {
  stages: FunnelStage[];
  bottleneckDetection: {
    bottleneckStage: string;
    dropoffImpactPercent: number;
    description: string;
  };
  historicalTrend: number[];
}

// 3. Contact Metrics
export interface ContactMetrics {
  totalContacts: number;
  newContacts: number;
  returningContacts: number;
  activeContacts: number;
  buyerVsPlayerRatio: Record<string, number>;
  lifecycleStages: Record<string, number>;
  avgLifetimeValue: number;
  avgBookingValue: number;
  repeatBookingRate: number;
  retentionRate: number;
  churnRisk: number;
  customerHealthScore: number;
}

// 4. Agent Performance
export interface AgentPerformance {
  agentId: string;
  callsMade: number;
  callsAnswered: number;
  avgHandleTimeSec: number;
  avgTalkTimeSec: number;
  avgResponseTimeSec: number;
  talkListenRatio: number;
  bookings: number;
  payments: number;
  revenue: number;
  conversionRate: number;
  qualityScore: number;
  csat: number;
  escalations: number;
  missedCalls: number;
  occupancy: number;
  idleTimeSeconds: number;
}

export interface AgentPerformanceWrapper {
  totalCallsMade: number;
  totalCallsAnswered: number;
  avgResponseTime: number;
  avgHandleTime: number;
  avgTalkTime: number;
  talkListenRatio: number;
  leaderboard: AgentPerformance[];
}

// 5. Team Performance
export interface TeamPerformance {
  teamName: string;
  revenue: number;
  bookings: number;
  payments: number;
  conversion: number;
  escalations: number;
  avgResponseTime: number;
  avgQuality: number;
  avgCsat: number;
  teamRanking: number;
}

export interface TeamPerformanceWrapper {
  activeAgents: number;
  inactiveAgents: number;
  teams: TeamPerformance[];
}

// 6. Voice AI Metrics
export interface VoiceAIMetrics {
  inboundCalls: number;
  outboundCalls: number;
  missedCalls: number;
  connectedCalls: number;
  droppedCalls: number;
  avgCallDurationSeconds: number;
  avgLatencySeconds: number;
  timeToFirstResponseSeconds: number;
  silencePercent: number;
  interruptions: number;
  speechSpeedWpm: number;
  speakingRatio: number;
  conversationSuccessRate: number;
  callCompletionRate: number;
}

// 7. AI Conversation Metrics
export interface AIConversationMetrics {
  intentDetectionAccuracy: number;
  entityExtractionAccuracy: number;
  slotFillingPercent: number;
  summaryQualityScore: number;
  hallucinationRate: number;
  questionAnswerRate: number;
  toolUsageCount: number;
  toolSuccessRate: number;
  retryCount: number;
  fallbackCount: number;
  unknownIntentPercent: number;
  conversationCompletionPercent: number;
}

// 8. Escalation Metrics
export interface EscalationMetrics {
  escalationRate: number;
  humanHandoffRate: number;
  avgResolutionTimeMinutes: number;
  resolutionPercent: number;
  escalationReasons: Record<string, number>;
  priorityDistribution: Record<string, number>;
}

// 9. Sentiment Metrics
export interface SentimentMetrics {
  avgSentimentScore: number;
  positivePercent: number;
  neutralPercent: number;
  negativePercent: number;
  sentimentTimeline: number[];
  peakSentiment: string;
  lowestSentiment: string;
  recoveryScore: number;
  sentimentVsConversion: Record<string, number>;
}

// 10. Pipeline Analytics
export interface PipelineAnalytics {
  pipelineValue: number;
  stageValue: Record<string, number>;
  avgDealSize: number;
  avgAgeDays: number;
  staleLeadsCount: number;
  forecastCloseDate: string;
  forecastRevenue: number;
  probabilityWeightedRevenue: number;
}

// 11. Booking Analytics
export interface BookingAnalytics {
  bookings: number;
  completed: number;
  pending: number;
  cancelled: number;
  rescheduled: number;
  avgGroupSize: number;
  roomUtilizationPercent: number;
  peakBookingHours: Record<string, number>;
  revenuePerBooking: number;
}

// 12. Channel Analytics
export interface ChannelStats {
  volume: number;
  conversion: number;
  revenue: number;
  avgResponseSec: number;
  openRate?: number;
  replyRate?: number;
}

export interface ChannelAnalytics {
  channels: Record<string, ChannelStats>;
}

// 13. Workflow Metrics
export interface WorkflowMetrics {
  autoQualification: number;
  autoBooking: number;
  autoCrmUpdates: number;
  autoFollowups: number;
  automationSuccessRate: number;
  automationFailureRate: number;
  manualOverrides: number;
  avgAutomationTimeSeconds: number;
}

// 14. LLM Metrics
export interface LLMMetrics {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  avgCostPerTurn: number;
  costPerConversation: number;
  avgLatencySeconds: number;
  firstTokenTimeSeconds: number;
  toolCallLatencySeconds: number;
  jsonSuccessRate: number;
  functionCallSuccessRate: number;
}

// 15. Knowledge Base Metrics
export interface KnowledgeBaseMetrics {
  retrievalSuccessRate: number;
  retrievalLatencySeconds: number;
  citationAccuracy: number;
  faqHitRate: number;
  knowledgeCoveragePercent: number;
  hallucinationReductionRatio: number;
}

// 16. Customer Satisfaction (CSAT)
export interface CustomerSatisfaction {
  csatScore: number;
  predictedCsatScore: number;
  nps: number;
  complaintRate: number;
  refundRequests: number;
  repeatPurchaseRate: number;
  lifetimeSpendAvg: number;
}

// 17. Predictive Analytics
export interface PredictiveAnalytics {
  bookingProbability: number;
  paymentProbability: number;
  closeProbability: number;
  upsellProbability: number;
  cancellationProbability: number;
  escalationRisk: number;
  churnRisk: number;
  customerLifetimeValuePrediction: number;
}

// 18. Operational Metrics
export interface OperationalMetrics {
  activeCalls: number;
  waitingCalls: number;
  queueLength: number;
  availableAgents: number;
  busy_agents?: number;
  avgQueueTimeSeconds: number;
  systemHealth: string;
  apiFailures: number;
  webhookFailures: number;
  toolFailures: number;
  llmErrors: number;
}

export interface FullDashboardState {
  kpis: ExecutiveKPIs;
  funnel: FunnelAnalytics;
  contacts: ContactMetrics;
  agents: AgentPerformanceWrapper;
  teams: TeamPerformanceWrapper;
  voice: VoiceAIMetrics;
  aiConvo: AIConversationMetrics;
  escalations: EscalationMetrics;
  sentiment: SentimentMetrics;
  pipeline: PipelineAnalytics;
  bookings: BookingAnalytics;
  channels: ChannelAnalytics;
  workflow: WorkflowMetrics;
  llm: LLMMetrics;
  knowledge: KnowledgeBaseMetrics;
  csat: CustomerSatisfaction;
  predictive: PredictiveAnalytics;
  operational: OperationalMetrics;
}
