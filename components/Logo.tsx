import React from 'react';

export const Logo: React.FC<{ className?: string }> = ({ className = "w-8 h-8" }) => (
  <img src="/logo_icon.png" alt="AutoSafe Logo" className={className} />
);
