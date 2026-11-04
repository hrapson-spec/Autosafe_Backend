import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { AppRoutes } from './App';
import './styles.css';

async function boot() {
  // Mock API (MSW) only when built or served with VITE_MOCK_API=1. First-pass app: no real backend yet.
  if (import.meta.env.VITE_MOCK_API) {
    const { startMockApi } = await import('./mocks/browser');
    await startMockApi();
  }
  createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </React.StrictMode>,
  );
}
void boot();
