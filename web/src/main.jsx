import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import DesktopStartup from './components/DesktopStartup.jsx'
import CloseChoice from './components/CloseChoiceDialog.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <CloseChoice>
      <DesktopStartup>
        <App />
      </DesktopStartup>
    </CloseChoice>
  </React.StrictMode>,
)
