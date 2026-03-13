/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}

declare module 'leaflet-draw' {
  export {}
}

declare module 'leaflet.heat' {
  import * as L from 'leaflet'
  
  interface HeatLayerOptions {
    radius?: number
    blur?: number
    maxZoom?: number
    max?: number
    minOpacity?: number
    gradient?: { [key: number]: string }
  }
  
  function heatLayer(
    latlngs: [number, number, number][],
    options?: HeatLayerOptions
  ): L.Layer
  
  export = heatLayer
}
