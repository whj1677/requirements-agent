import React from 'react';
import { createRoot } from 'react-dom/client';
import { Prototype } from './Prototype';

export function renderPrototype(root: HTMLElement) { createRoot(root).render(<Prototype />); }
