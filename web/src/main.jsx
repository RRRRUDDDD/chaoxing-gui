import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import DesktopStartup from './components/DesktopStartup.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <DesktopStartup>
      <App />
    </DesktopStartup>
  </React.StrictMode>,
)
