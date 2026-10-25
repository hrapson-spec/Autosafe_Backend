import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { ApiError, api } from '../api/client';
import type { Assessment, Booking, BriefingExtras, Decision, DecisionType, FeedbackValue } from '../api/types';
import { BriefingView } from '../components/BriefingView';
import { Alert, Spinner } from '../components/ui';

interface Loaded { booking: Booking; decisions: Decision[]; versions: Assessment[]; extras: BriefingExtras | null }

export const BriefingPage: React.FC<{ pollMs?: number }> = ({ pollMs = 3000 }) => {
  const { id = '' } = useParams();
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [booking, decisions, versions] = await Promise.all([api.booking(id), api.decisions(id), api.assessments(id)]);
      // The briefing endpoint is PROPOSED (not in the contract yet). Anything but success means "not provided".
      const extras = await api.briefingExtras(id).catch(() => null);
      setData({ booking, decisions: decisions.items, versions: versions.items, extras });
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not load this briefing.');
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const pending = data?.booking.assessment?.state === 'pending';
  useEffect(() => {
    if (!pending) return;
    const t = setTimeout(() => void load(), pollMs);
    return () => clearTimeout(t);
  }, [pending, data, load, pollMs]);

  if (error) return <Alert kind="error">{error}</Alert>;
  if (!data) return <Spinner label="Loading briefing" />;

  const onDecide = async (type: DecisionType, note: string) => {
    await api.decide(id, type, note);
    await load();
  };
  const onFeedback = async (v: FeedbackValue) => { await api.feedback(id, v); };
  const onRefresh = async () => {
    setRefreshing(true);
    setRefreshError(null);
    try {
      await api.refreshAssessment(id);
      await load();
    } catch (e) {
      setRefreshError(e instanceof ApiError ? e.message : 'Could not refresh the assessment.');
    } finally {
      setRefreshing(false);
    }
  };
  return (
    <BriefingView
      booking={data.booking} decisions={data.decisions} extras={data.extras} versions={data.versions}
      backTo="/app/bookings" onDecide={onDecide} onFeedback={onFeedback} onRefresh={onRefresh} refreshing={refreshing} refreshError={refreshError}
    />
  );
};
