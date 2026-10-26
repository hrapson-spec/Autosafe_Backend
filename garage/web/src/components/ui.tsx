import React, { useId } from 'react';

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';

const VARIANT: Record<ButtonVariant, string> = {
  primary: 'bg-slate-900 text-white hover:bg-slate-700 disabled:bg-slate-400',
  secondary: 'bg-white text-slate-900 border border-slate-400 hover:bg-slate-100 disabled:text-slate-400',
  danger: 'bg-white text-red-800 border border-red-700 hover:bg-red-50 disabled:text-red-300',
  ghost: 'bg-transparent text-slate-800 hover:bg-slate-200 disabled:text-slate-400',
};

export const Button: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }> = ({
  variant = 'primary', className = '', type = 'button', ...rest
}) => (
  <button
    type={type}
    className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed ${VARIANT[variant]} ${className}`}
    {...rest}
  />
);

interface FieldProps {
  label: string;
  hint?: string;
  error?: string | null;
}

export const TextField = React.forwardRef<HTMLInputElement, FieldProps & React.InputHTMLAttributes<HTMLInputElement>>(
  ({ label, hint, error, className = '', id, ...rest }, ref) => {
    const auto = useId();
    const fid = id ?? auto;
    const describedBy = [hint ? `${fid}-hint` : '', error ? `${fid}-err` : ''].filter(Boolean).join(' ') || undefined;
    return (
      <div className={className}>
        <label htmlFor={fid} className="block text-sm font-semibold text-slate-900">{label}</label>
        {hint && <p id={`${fid}-hint`} className="text-sm text-slate-600">{hint}</p>}
        <input
          ref={ref}
          id={fid}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className="mt-1 block min-h-11 w-full rounded-lg border border-slate-400 bg-white px-3 py-2 text-base text-slate-900"
          {...rest}
        />
        {error && <p id={`${fid}-err`} className="mt-1 text-sm font-medium text-red-800">{error}</p>}
      </div>
    );
  },
);
TextField.displayName = 'TextField';

export const SelectField: React.FC<FieldProps & React.SelectHTMLAttributes<HTMLSelectElement>> = ({
  label, hint, error, className = '', id, children, ...rest
}) => {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <div className={className}>
      <label htmlFor={fid} className="block text-sm font-semibold text-slate-900">{label}</label>
      {hint && <p className="text-sm text-slate-600">{hint}</p>}
      <select id={fid} aria-invalid={error ? true : undefined} className="mt-1 block min-h-11 w-full rounded-lg border border-slate-400 bg-white px-3 py-2 text-base" {...rest}>
        {children}
      </select>
      {error && <p className="mt-1 text-sm font-medium text-red-800">{error}</p>}
    </div>
  );
};

export const Card: React.FC<React.HTMLAttributes<HTMLElement> & { as?: 'section' | 'div' | 'article' }> = ({
  as: Tag = 'section', className = '', ...rest
}) => <Tag className={`rounded-xl border border-slate-300 bg-white p-4 shadow-sm sm:p-6 ${className}`} {...rest} />;

export const Alert: React.FC<{ kind?: 'error' | 'info' | 'success'; children: React.ReactNode; className?: string }> = ({
  kind = 'info', children, className = '',
}) => {
  const colours = {
    error: 'border-red-700 bg-red-50 text-red-900',
    info: 'border-slate-400 bg-slate-50 text-slate-900',
    success: 'border-emerald-700 bg-emerald-50 text-emerald-900',
  }[kind];
  return (
    <div role={kind === 'error' ? 'alert' : 'status'} className={`rounded-lg border-l-4 px-4 py-3 text-sm ${colours} ${className}`}>
      {children}
    </div>
  );
};

export const Spinner: React.FC<{ label?: string }> = ({ label = 'Loading' }) => (
  <p role="status" className="text-sm text-slate-600">{label}…</p>
);

export const Chip: React.FC<{ tone?: 'neutral' | 'low' | 'medium' | 'high' | 'warn' | 'ok'; children: React.ReactNode }> = ({
  tone = 'neutral', children,
}) => {
  const c = {
    neutral: 'bg-slate-200 text-slate-900',
    low: 'bg-emerald-100 text-emerald-900 border border-emerald-700',
    medium: 'bg-amber-100 text-amber-900 border border-amber-700',
    high: 'bg-red-100 text-red-900 border border-red-700',
    warn: 'bg-amber-50 text-amber-900 border border-amber-600',
    ok: 'bg-emerald-50 text-emerald-900 border border-emerald-600',
  }[tone];
  return <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${c}`}>{children}</span>;
};
