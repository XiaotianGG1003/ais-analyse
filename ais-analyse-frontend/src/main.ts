import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'leaflet/dist/leaflet.css'
import 'leaflet-draw/dist/leaflet.draw.css'
import './style.css'
import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
