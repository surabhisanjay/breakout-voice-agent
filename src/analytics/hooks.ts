import { useState, useEffect, useCallback } from 'react';
import { FullDashboardState, GlobalFilters } from './types';

const REST_API_BASE = '/api/analytics';
const WS_ENDPOINT = 'ws://127.0.0.1:8030';

/**
 * Reusable React Hook to connect to the realtime Voice CRM / Sales OS WebSocket broker.
 */
export function useRealtimeAnalytics(onEventReceived?: (eventType: string, payload: any) => void) {
  const [isConnected, setIsConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<{ eventType: string; payload: any } | null>(null);

  useEffect(() => {
    let ws: WebSocket;

    function connect() {
      ws = new WebSocket(WS_ENDPOINT);

      ws.onopen = () => {
        setIsConnected(true);
        console.log('Realtime analytics WebSocket connection opened.');
      };

      ws.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data);
          const eventType = parsed.event_type;
          const payload = parsed.payload;
          
          setLastEvent({ eventType, payload });
          if (onEventReceived) {
            onEventReceived(eventType, payload);
          }
        } catch (e) {
          console.error('Failed to parse WebSocket incoming event payload:', e);
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        console.log('Realtime analytics WebSocket disconnected. Retrying in 5 seconds...');
        setTimeout(connect, 5000);
      };

      ws.onerror = (err) => {
        console.error('WebSocket connection error:', err);
        ws.close();
      };
    }

    connect();

    return () => {
      if (ws) {
        ws.close();
      }
    };
  }, [onEventReceived]);

  return { isConnected, lastEvent };
}

/**
 * Reusable Hook to query complete historical dashboard KPIs with dynamic global filters.
 */
export function useDashboardMetrics(initialFilters: GlobalFilters = {}) {
  const [metrics, setMetrics] = useState<FullDashboardState | null>(null);
  const [filters, setFilters] = useState<GlobalFilters>(initialFilters);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchMetrics = useCallback(async (currentFilters: GlobalFilters) => {
    setIsLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (currentFilters.startDate && currentFilters.endDate) {
        params.append('start_date', currentFilters.startDate);
        params.append('end_date', currentFilters.endDate);
      }
      if (currentFilters.agentId) params.append('agent_id', currentFilters.agentId);
      if (currentFilters.teamId) params.append('team_id', currentFilters.teamId);
      if (currentFilters.location) params.append('location', currentFilters.location);
      if (currentFilters.room) params.append('room', currentFilters.room);
      if (currentFilters.sentiment) params.append('sentiment', currentFilters.sentiment);
      if (currentFilters.priority) params.append('priority', currentFilters.priority);
      if (currentFilters.channel) params.append('channel', currentFilters.channel);

      const url = `${REST_API_BASE}/full?${params.toString()}`;
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`Analytics API returned status ${response.status}`);
      }
      const data = await response.json();
      setMetrics(data);
    } catch (err: any) {
      setError(err.message || 'Failed to retrieve dashboard analytics');
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Fetch when filters change
  useEffect(() => {
    fetchMetrics(filters);
  }, [filters, fetchMetrics]);

  // Hook up websocket for live invalidation and refresh
  useRealtimeAnalytics(
    useCallback(() => {
      // Refresh statistics automatically upon receiving any realtime business event
      fetchMetrics(filters);
    }, [filters, fetchMetrics])
  );

  const updateFilters = (newFilters: Partial<GlobalFilters>) => {
    setFilters((prev) => ({ ...prev, ...newFilters }));
  };

  return { metrics, filters, updateFilters, isLoading, error, refresh: () => fetchMetrics(filters) };
}
