import React, { useState } from 'react';
import { ApiError, api } from '../api/client';
import { PageHeading } from '../components/Layout';
import { Alert, Button, Card, TextField } from '../components/ui';

export const SignInPage: React.FC = () => {
  const [mode, setMode] = useState<'sign_in' | 'sign_up'>('sign_in');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [workshop, setWorkshop] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === 'sign_up') await api.signup({ invite_code: code.trim(), workshop_name: workshop.trim(), email: email.trim() });
      else await api.requestLoginLink(email.trim());
      setSentTo(email.trim());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  };

  if (sentTo) {
    return (
      <Card className="mx-auto max-w-lg">
        <PageHeading>Check your email</PageHeading>
        <p role="status" className="mt-3">
          If <strong>{sentTo}</strong> can sign in, a link is on its way. It works once and expires after 15 minutes.
          Opening it brings you to a page where you confirm.
        </p>
        {import.meta.env.VITE_MOCK_API && <div className="mt-4 rounded border border-amber-300 bg-amber-50 p-3">
          <p className="text-sm">Fixture mailbox: no email was sent. Continue here to confirm the invented sign-in link.</p>
          <Button className="mt-2" onClick={() => {
            const mock = (window as unknown as { __garageMock: { lastTokenFor: (email: string) => string; consumeToken: (token: string) => unknown; fastAssessments: () => void } }).__garageMock;
            const token = mock.lastTokenFor(sentTo);
            if (!token) { setError('No fixture link is available. Create a workshop with invite PILOT-0001 first.'); setSentTo(null); return; }
            mock.fastAssessments(); mock.consumeToken(token); window.location.assign('/app');
          }}>Confirm fixture sign-in</Button>
        </div>}
        <Button className="mt-4" variant="secondary" onClick={() => setSentTo(null)}>Use a different address</Button>
      </Card>
    );
  }

  const canSend = !!email.trim() && (mode === 'sign_in' || (!!code.trim() && !!workshop.trim()));
  return (
    <Card className="mx-auto max-w-lg">
      <PageHeading>Sign in</PageHeading>
      <form onSubmit={submit} className="mt-4 space-y-4" noValidate>
        <fieldset>
          <legend className="text-sm font-semibold">I want to</legend>
          <div className="mt-2 flex flex-wrap gap-4">
            <label className="flex min-h-11 items-center gap-2"><input type="radio" name="mode" checked={mode === 'sign_in'} onChange={() => setMode('sign_in')} /> Sign in</label>
            <label className="flex min-h-11 items-center gap-2"><input type="radio" name="mode" checked={mode === 'sign_up'} onChange={() => setMode('sign_up')} /> Create a workshop with an invite</label>
          </div>
        </fieldset>
        <TextField label="Your work email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        {mode === 'sign_up' && (
          <>
            <TextField label="Workshop name" hint="Shown to your team. Up to 120 characters." maxLength={120} autoComplete="organization" value={workshop} onChange={(e) => setWorkshop(e.target.value)} required />
            <TextField label="Invite code" hint="Supplied by AutoSafe for the pilot." autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} required />
          </>
        )}
        {error && <Alert kind="error">{error}</Alert>}
        <Button type="submit" disabled={busy || !canSend}>{busy ? 'Sending…' : 'Email me a sign-in link'}</Button>
      </form>
    </Card>
  );
};
