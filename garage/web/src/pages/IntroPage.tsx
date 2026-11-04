import React from 'react';
import { Link } from 'react-router-dom';
import { PageHeading } from '../components/Layout';
import { Card } from '../components/ui';

export const IntroPage: React.FC = () => (
  <div className="space-y-6">
    <div>
      <PageHeading>See which MOT bookings need a closer look</PageHeading>
      <p className="mt-3 max-w-2xl text-lg text-slate-800">
        Add the week&apos;s bookings by registration. AutoSafe Garage ranks the initial MOTs by an estimate built from each
        vehicle&apos;s recorded MOT history, and gives a short briefing for each one.
      </p>
      <div className="mt-5 flex flex-wrap gap-3">
        <Link to="/demo" className="inline-flex min-h-11 items-center rounded-lg bg-slate-900 px-5 py-2 font-semibold text-white">Try the fictional demo</Link>
        <Link to="/sign-in" className="inline-flex min-h-11 items-center rounded-lg border border-slate-500 bg-white px-5 py-2 font-semibold">Sign in or create a workshop</Link>
      </div>
    </div>
    <div className="grid gap-4 sm:grid-cols-3">
      <Card as="div"><h2 className="font-bold">Registration only</h2><p className="mt-1 text-sm text-slate-700">No customer names, emails or phone numbers are collected or stored.</p></Card>
      <Card as="div"><h2 className="font-bold">Plainly labelled</h2><p className="mt-1 text-sm text-slate-700">Each figure is the estimated chance of failing or needing rectification during the MOT. It is not an inspection or a guarantee.</p></Card>
      <Card as="div"><h2 className="font-bold">Your decision</h2><p className="mt-1 text-sm text-slate-700">Record what you decided and why. The history shows who decided and when.</p></Card>
    </div>
    <p className="text-sm text-slate-600">Invite-only pilot for a small number of workshops. Supported: Class 4 initial MOTs on vehicles with at least one earlier MOT. Retests and service-only bookings are listed but not assessed.</p>
  </div>
);
