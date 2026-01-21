import React from 'react';
import { Link } from 'react-router-dom';
import { Helmet } from 'react-helmet-async';
import { ArrowLeft } from '../Icons';
import { Logo } from '../Logo';
import { Button } from '../ui';

interface GuideLayoutProps {
  title: string;
  metaTitle: string;
  metaDescription: string;
  canonicalPath: string;
  lastUpdated: string;
  children: React.ReactNode;
}

const GuideLayout: React.FC<GuideLayoutProps> = ({
  title,
  metaTitle,
  metaDescription,
  canonicalPath,
  lastUpdated,
  children
}) => {
  return (
    <div className="min-h-screen bg-[#F0F0F0] text-slate-900">
      <Helmet>
        <title>{metaTitle} | AutoSafe</title>
        <meta name="description" content={metaDescription} />
        <meta property="og:title" content={metaTitle} />
        <meta property="og:description" content={metaDescription} />
        <link rel="canonical" href={`https://autosafe.co.uk${canonicalPath}`} />
      </Helmet>

      {/* Header */}
      <nav className="w-full bg-transparent pt-8 pb-4">
        <div className="max-w-4xl mx-auto px-4 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3 group">
            <Logo className="text-slate-900 w-8 h-8" />
            <span className="font-serif font-bold text-2xl text-slate-900">AutoSafe</span>
          </Link>
          <Link
            to="/"
            className="flex items-center gap-2 text-slate-500 hover:text-slate-900 transition-colors text-sm"
          >
            <ArrowLeft className="w-4 h-4" aria-hidden="true" />
            Back to Home
          </Link>
        </div>
      </nav>

      {/* Content */}
      <main className="max-w-4xl mx-auto px-4 py-8">
        <article className="bg-white rounded-2xl shadow-sm p-8 md:p-12">
          <h1 className="font-serif text-4xl font-medium text-slate-900 mb-2">{title}</h1>
          <p className="text-slate-400 text-sm mb-8">Last updated: {lastUpdated}</p>

          {children}

          {/* CTA Section */}
          <div className="mt-12 p-8 bg-slate-900 rounded-2xl text-center">
            <h2 className="font-serif text-2xl font-medium text-white mb-4">
              Ready to check your vehicle?
            </h2>
            <p className="text-slate-300 mb-6 max-w-md mx-auto">
              Use our free MOT history checker to see your vehicle's risk score and get personalized advice.
            </p>
            <Link to="/">
              <Button variant="secondary" size="lg">
                Check Your Vehicle Free
              </Button>
            </Link>
          </div>
        </article>

        {/* Footer */}
        <div className="mt-8 text-center">
          <Link to="/" className="text-slate-500 hover:text-slate-900 transition-colors text-sm">
            &larr; Back to AutoSafe
          </Link>
        </div>
      </main>
    </div>
  );
};

export default GuideLayout;
