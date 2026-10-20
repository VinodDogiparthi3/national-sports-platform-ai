import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

const root = createRoot(document.getElementById('root'))

function mount(Component) {
  root.render(<StrictMode><Component /></StrictMode>)
}

if (import.meta.env.DEV) {
  import('./platform/PlatformShell.jsx').then(({ default: PlatformShell }) => mount(PlatformShell))
} else {
  mount(App)
}
