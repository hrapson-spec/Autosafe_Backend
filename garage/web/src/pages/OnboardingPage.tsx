import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { PageHeading } from '../components/Layout';
import { Button, Card } from '../components/ui';
import { NOTES_HINT } from '../lib/estimate';

/** The workshop is created at signup (with the invite code); this is the "what next" step. */
export const OnboardingPage: React.FC = () => {
  const { me } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <p className="text-sm font-semibold text-slate-600">Step 2 of 2</p>
      <Card>
        <PageHeading>Your workshop is ready</PageHeading>
        <p className="mt-2 text-slate-700"><strong>{me?.workshop.name}</strong> has been created. Choose how to begin.</p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="rounded-lg border border-slate-300 p-4">
            <h2 className="font-bold">Look around first</h2>
            <p className="mt-1 text-sm text-slate-700">A fictional demo with invented vehicles. Nothing is saved to your workshop.</p>
            <Link to="/demo" className="mt-3 inline-flex min-h-11 items-center rounded-lg border border-slate-400 bg-white px-4 py-2 text-sm font-semibold">Open the fictional demo</Link>
          </div>
          <div className="rounded-lg border border-slate-300 p-4">
            <h2 className="font-bold">Import your bookings</h2>
            <p className="mt-1 text-sm text-slate-700">Paste registrations, upload a CSV or type them in. Up to 200 rows.</p>
            <Button className="mt-3" onClick={() => navigate('/app/import')}>Import first bookings</Button>
          </div>
        </div>
        <p className="mt-4 text-sm text-slate-700" data-testid="notes-hint">Notes you add to decisions are free text. {NOTES_HINT}</p>
        <p className="mt-2"><Link to="/app/bookings" className="text-sm font-semibold underline">Skip to the booking list</Link></p>
      </Card>
    </div>
  );
};
