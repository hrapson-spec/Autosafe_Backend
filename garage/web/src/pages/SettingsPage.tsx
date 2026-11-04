import React, { useCallback, useEffect, useState } from 'react';
import { ApiError, api } from '../api/client';
import type { Member, Workshop } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { PageHeading } from '../components/Layout';
import { Alert, Button, Card, Chip, Spinner, TextField } from '../components/ui';
import { formatTimestamp } from '../lib/dates';

export const SettingsPage: React.FC = () => {
  const { me, refresh } = useAuth();
  const isOwner = me?.role === 'owner';
  const [members, setMembers] = useState<Member[] | null>(null);
  const [workshop, setWorkshop] = useState<Workshop | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [name, setName] = useState(me?.workshop.name ?? '');
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, w] = await Promise.all([api.staff(), api.workshop()]);
      setMembers(s.items);
      setWorkshop(w);
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Could not load the team.'); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null);
    setNotice(null);
    try {
      await fn();
      await load();
      setNotice(ok);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong.');
    }
  };

  return (
    <div className="space-y-4">
      <PageHeading>Team and settings</PageHeading>
      {error && <Alert kind="error">{error}</Alert>}
      {notice && <Alert kind="success">{notice}</Alert>}

      <Card aria-labelledby="ws-h">
        <h2 id="ws-h" className="text-lg font-bold">Workshop</h2>
        {isOwner ? (
          <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); void act(async () => { await api.renameWorkshop(name.trim()); await refresh(); }, 'Workshop name saved.'); }}>
            <TextField className="min-w-[14rem] flex-1" label="Workshop name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
            <Button type="submit" variant="secondary" disabled={!name.trim() || name.trim() === me?.workshop.name}>Save name</Button>
          </form>
        ) : (
          <p className="mt-2">{me?.workshop.name}</p>
        )}
        {workshop && (
          <p className="mt-4 text-sm text-slate-700" data-testid="usage">
            Assessments today ({workshop.usage.date}): {workshop.usage.assessments_used} of {workshop.usage.assessments_limit} used, {workshop.usage.assessments_remaining} left.
          </p>
        )}
      </Card>

      <Card aria-labelledby="team-h">
        <h2 id="team-h" className="text-lg font-bold">Team</h2>
        {!isOwner && <p className="text-sm text-slate-600">Only the owner can add or remove people.</p>}
        {!members ? <Spinner label="Loading team" /> : (
          <ul className="mt-3 divide-y divide-slate-200" aria-label="Team members">
            {members.map((m) => (
              <li key={m.membership_id} className="flex flex-wrap items-center justify-between gap-2 py-2" data-testid="member">
                <span>
                  <span className="font-semibold">{m.email}</span>{' '}
                  <Chip>{m.role === 'owner' ? 'Owner' : 'Staff'}</Chip>
                  <span className="block text-sm text-slate-600">Added {formatTimestamp(m.created_at)}</span>
                </span>
                {isOwner && m.role !== 'owner' && (
                  confirmRemove === m.membership_id ? (
                    <span className="flex gap-2" role="group" aria-label={`Confirm removing ${m.email}`}>
                      <Button variant="danger" onClick={() => { setConfirmRemove(null); void act(() => api.removeStaff(m.membership_id), `${m.email} removed. Their sessions are ended.`); }}>Yes, remove {m.email}</Button>
                      <Button variant="ghost" onClick={() => setConfirmRemove(null)}>Cancel</Button>
                    </span>
                  ) : (
                    <Button variant="danger" aria-label={`Remove ${m.email}`} onClick={() => setConfirmRemove(m.membership_id)}>Remove</Button>
                  )
                )}
              </li>
            ))}
          </ul>
        )}
        {isOwner && (
          <form className="mt-4 flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); void act(async () => { await api.addStaff(email.trim()); setEmail(''); }, 'Added. A sign-in link has been emailed to them.'); }}>
            <TextField className="min-w-[14rem] flex-1" label="Add a colleague by email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            <Button type="submit" disabled={!email.trim()}>Add and email a link</Button>
          </form>
        )}
      </Card>
    </div>
  );
};
