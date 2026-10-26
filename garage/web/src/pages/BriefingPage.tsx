import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { ApiError, api } from '../api/client';
import type { Assessment, Booking, Briefing, Decision, DecisionType, FeedbackReason, FeedbackValue } from '../api/types';
import { BriefingView } from '../components/BriefingView';
import { Alert, Spinner } from '../components/ui';

interface Loaded { booking: Booking; decisions: Decision[]; versions: Assessment[]; briefing: Briefing | null }

export const BriefingPage: React.FC<{ pollMs?: number }> = ({ pollMs = 3000 }) => {
  const { id = '' } = useParams();
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [booking, decisions, versions] = await Promise.all([api.booking(id), api.decisions(id), api.assessments(id)]);
      // A briefing that cannot be loaded must not hide the booking: it becomes an explicit evidence gap.
      const briefing = await api.briefing(id).catch(() => null);
      setData({ booking, decisions: decisions.items, versions: versions.items, briefing });
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not load this briefing.');
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const pending = data?.booking.assessment?.state === 'pending' || data?.briefing?.status === 'pending';
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
  const onFeedback = async (value: FeedbackValue, reason_code: FeedbackReason | null) => {
    try {
      await api.feedback(id, { value, reason_code, briefing_version: data.briefing?.briefing_version ?? null });
    } catch (e) {
      await load();      // e.g. the briefing was regenerated: show the current one, then report the failure
      throw e;
    }
  };
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
      booking={data.booking} decisions={data.decisions} briefing={data.briefing} versions={data.versions}
      backTo="/app/bookings" onDecide={onDecide} onFeedback={onFeedback} onRefresh={onRefresh} refreshing={refreshing} refreshError={refreshError}
    />
  );
};
