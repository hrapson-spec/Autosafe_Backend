import React from 'react';

interface CardProps {
  children: React.ReactNode;
  className?: string;
  padding?: 'sm' | 'md' | 'lg';
  variant?: 'default' | 'dark';
}

interface CardHeaderProps {
  icon?: React.ReactNode;
  iconBg?: string;
  title: string;
  className?: string;
}

interface CardContentProps {
  children: React.ReactNode;
  className?: string;
}

const Card: React.FC<CardProps> & {
  Header: React.FC<CardHeaderProps>;
  Content: React.FC<CardContentProps>;
} = ({
  children,
  className = '',
  padding = 'md',
  variant = 'default',
}) => {
  const paddingStyles = {
    sm: 'p-4',
    md: 'p-6',
    lg: 'p-8',
  };

  const variantStyles = {
    default: 'bg-white border border-slate-100',
    dark: 'bg-slate-900 text-white',
  };

  return (
    <div className={`rounded-2xl shadow-sm ${variantStyles[variant]} ${paddingStyles[padding]} ${className}`}>
      {children}
    </div>
  );
};

const CardHeader: React.FC<CardHeaderProps> = ({
  icon,
  iconBg = 'bg-slate-100',
  title,
  className = '',
}) => (
  <div className={`flex items-center gap-3 mb-4 ${className}`}>
    {icon && (
      <div className={`p-2 rounded-lg ${iconBg}`}>
        {icon}
      </div>
    )}
    <h3 className="text-slate-900 font-semibold">{title}</h3>
  </div>
);

const CardContent: React.FC<CardContentProps> = ({
  children,
  className = '',
}) => (
  <div className={className}>
    {children}
  </div>
);

Card.Header = CardHeader;
Card.Content = CardContent;

export default Card;
