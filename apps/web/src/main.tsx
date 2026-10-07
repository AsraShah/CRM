import { createRoot } from 'react-dom/client'

import '@fontsource-variable/inter/wght.css'

import { App } from './app/App'
import './styles/tokens.css'

const container = document.getElementById('root')
if (!container) {
  throw new Error('The #root element is missing from index.html.')
}

createRoot(container).render(<App />)
