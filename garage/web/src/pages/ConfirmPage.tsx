import React, { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ApiError, api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { PageHeading } from '../components/Layout';
import { Alert, Button, Card } from '../components/ui';

/** Landing page for the emailed link. Nothing is consumed until the person presses the button. */
export const ConfirmPage: React.FC = () => {
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';
  const { setMe } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const me = await api.consumeLink(token);
      setMe(me);
      // First visit (no bookings yet) goes to onboarding; otherwise straight to the list.
      const any = await api.hasBookings().catch(() => true);
      navigate(any ? '/app/bookings' : '/onboarding', { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
      setBusy(false);
    }
  };

  return (
    <Card className="mx-auto max-w-lg">
      <PageHeading>Confirm sign-in</PageHeading>
      {token ? (
        <>
          <p className="mt-3">Press the button to finish signing in on this device.</p>
          {error && <Alert kind="error" className="mt-3">{error}</Alert>}
          <Button className="mt-4" onClick={confirm} disabled={busy}>{busy ? 'Signing in…' : 'Confirm and sign in'}</Button>
        </>
      ) : (
        <Alert kind="error" className="mt-3">This link is missing its code. Request a new sign-in link.</Alert>
      )}
    </Card>
  );
};
