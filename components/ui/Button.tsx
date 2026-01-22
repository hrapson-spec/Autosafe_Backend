import React from 'react';
import { Loader2 } from '../Icons';

interface ButtonProps {
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
  loadingText?: string;
  disabled?: boolean;
  fullWidth?: boolean;
  type?: 'button' | 'submit' | 'reset';
  children: React.ReactNode;
  onClick?: () => void;
  className?: string;
  'aria-label'?: string;
}

const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  loading = false,
  loadingText = 'Loading...',
  disabled = false,
  fullWidth = false,
  type = 'button',
  children,
  onClick,
  className = '',
  'aria-label': ariaLabel,
}) => {
  const baseStyles = "inline-flex items-center justify-center gap-2 font-semibold tracking-wide transition-all focus:ring-2 focus:ring-offset-2 disabled:opacity-70 disabled:cursor-not-allowed";

  const variantStyles = {
    primary: "bg-slate-900 text-white shadow-lg shadow-slate-900/10 hover:bg-black hover:shadow-xl hover:scale-[1.01] active:scale-[0.99] focus:ring-slate-900",
    secondary: "bg-white text-slate-900 border border-slate-200 hover:bg-slate-50 hover:border-slate-300 focus:ring-slate-500",
    outline: "bg-transparent text-slate-900 border-2 border-slate-900 hover:bg-slate-900 hover:text-white focus:ring-slate-900",
    ghost: "bg-transparent text-slate-600 hover:text-slate-900 hover:bg-slate-100 focus:ring-slate-500",
  };

  const sizeStyles = {
    sm: "px-4 py-2 text-sm rounded-lg",
    md: "px-6 py-3 text-sm rounded-lg",
    lg: "px-6 py-4 text-sm rounded-full uppercase",
  };

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      aria-label={ariaLabel}
      aria-busy={loading}
      className={`${baseStyles} ${variantStyles[variant]} ${sizeStyles[size]} ${fullWidth ? 'w-full' : ''} ${className}`}
    >
      {loading ? (
        <>
          <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
          <span>{loadingText}</span>
        </>
      ) : (
        children
      )}
    </button>
  );
};

export default Button;
