import React, { forwardRef } from 'react';

interface InputProps {
  id: string;
  label: string;
  type?: 'text' | 'email' | 'tel' | 'number';
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  error?: string;
  success?: boolean;
  required?: boolean;
  maxLength?: number;
  uppercase?: boolean;
  icon?: React.ReactNode;
  className?: string;
  inputClassName?: string;
  'aria-label'?: string;
}

const Input = forwardRef<HTMLInputElement, InputProps>(({
  id,
  label,
  type = 'text',
  placeholder,
  value,
  onChange,
  onBlur,
  error,
  success,
  required = false,
  maxLength,
  uppercase = false,
  icon,
  className = '',
  inputClassName = '',
  'aria-label': ariaLabel,
}, ref) => {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newValue = uppercase ? e.target.value.toUpperCase() : e.target.value;
    onChange(newValue);
  };

  const baseInputStyles = "w-full px-4 py-3.5 bg-white border rounded-lg appearance-none transition-all text-slate-900 placeholder-slate-400";

  const stateStyles = error
    ? "border-red-300 focus:ring-2 focus:ring-red-500 focus:ring-offset-2 focus:border-red-500"
    : success
    ? "border-green-300 focus:ring-2 focus:ring-green-500 focus:ring-offset-2 focus:border-green-500"
    : "border-slate-200 focus:ring-2 focus:ring-slate-900 focus:ring-offset-2 focus:border-slate-900";

  return (
    <div className={className}>
      <label
        htmlFor={id}
        className="block text-sm text-slate-600 mb-1.5 ml-1"
      >
        {icon && <span className="inline-flex items-center mr-1 align-middle">{icon}</span>}
        {label}
        {required && <span className="text-red-500 ml-0.5" aria-hidden="true">*</span>}
      </label>
      <input
        ref={ref}
        id={id}
        type={type}
        placeholder={placeholder}
        value={value}
        onChange={handleChange}
        onBlur={onBlur}
        maxLength={maxLength}
        required={required}
        aria-label={ariaLabel}
        aria-invalid={error ? 'true' : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        className={`${baseInputStyles} ${stateStyles} ${uppercase ? 'uppercase font-medium tracking-wide' : ''} ${inputClassName}`}
      />
      {error && (
        <p
          id={`${id}-error`}
          className="mt-1.5 ml-1 text-sm text-red-600 flex items-center gap-1"
          role="alert"
        >
          <svg className="w-4 h-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
          {error}
        </p>
      )}
    </div>
  );
});

Input.displayName = 'Input';

export default Input;
