import './mail-automation.css';
import React from 'react';
import {createRoot} from 'react-dom/client';
import {AgentApp} from './AgentApp';
import {CrowWidget} from './CrowWidget';
import './crow-widget.css';
import './style.css';
import './tools.css';
import './canvas-sync.css';
import './canvas-reader.css';
import './agent.css';
import './agent-pending.css';
import './agent-recovery.css';
import './conversation-export.css';
const widget = new URLSearchParams(window.location.search).get('widget') === '1';
if (widget) document.documentElement.classList.add('crow-widget-mode');
createRoot(document.getElementById('root')!).render(<React.StrictMode>{widget ? <CrowWidget/> : <AgentApp/>}</React.StrictMode>);

import './mail.css';

import './mail-workspace.css';
import './mail-compose.css';
