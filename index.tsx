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

// First-party acquisition measurement (OA-005). Order matters: the landing
// handoff parameters are read and removed from the address bar here, before
// React renders and therefore before the first page view is sent. The
// transport installs only when ACQUISITION_COLLECTOR_ENABLED is true (it is
// false, so the bundler also drops the transport code from the build);
// initAcquisitionLanding then only strips `al`/`src` if present.
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