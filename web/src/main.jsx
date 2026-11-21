import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import DesktopStartup from './components/DesktopStartup.jsx'
import CloseChoice from './components/CloseChoiceDialog.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <CloseChoice>
        <DesktopStartup>
          <App />
        </DesktopStartup>
      </CloseChoice>
    </ErrorBoundary>
  </React.StrictMode>,
)
