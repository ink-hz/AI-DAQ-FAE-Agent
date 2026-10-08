import React from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import { IdentityGate } from './IdentityGate';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <IdentityGate />
  </React.StrictMode>,
);
