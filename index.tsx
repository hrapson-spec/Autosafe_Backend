import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import './styles.css';
import App from './App';
import { ACQUISITION_COLLECTOR_ENABLED, installAcquisitionTransport } from './utils/acquisitionEvents';
import { initAcquisitionLanding } from './utils/acquisitionLanding';

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error("Could not find root element to mount to");
}

// The shared head script owns bounded attribution and objection handling.
// Install the gated transport, then copy that context before React renders.
// Every send rechecks the live shared context, expiry and objection state.
if (ACQUISITION_COLLECTOR_ENABLED) installAcquisitionTransport();
initAcquisitionLanding();

const root = ReactDOM.createRoot(rootElement);
root.render(
  <React.StrictMode>
    <HelmetProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </HelmetProvider>
  </React.StrictMode>
);