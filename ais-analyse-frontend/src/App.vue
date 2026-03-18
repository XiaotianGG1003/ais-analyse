<script setup lang="ts">
import { ref } from 'vue'
import { useAppStore } from '@/stores/app'
import TopNavBar from '@/components/TopNavBar.vue'
import LeftPanel from '@/components/LeftPanel.vue'
import MapView from '@/components/MapView.vue'
import RightPanel from '@/components/RightPanel.vue'
import StatusBar from '@/components/StatusBar.vue'

const store = useAppStore()
const mapViewRef = ref<InstanceType<typeof MapView>>()

function onQueryTrack() {
  mapViewRef.value?.queryTrack()
}

function onAreaDetect() {
  mapViewRef.value?.toggleAreaDraw()
}

function onPredict() {
  mapViewRef.value?.startPrediction()
}

function onCalcDistance(shipA: number, shipB: number) {
  mapViewRef.value?.calcDistance(shipA, shipB)
}

function onToggleHeatmap() {
  mapViewRef.value?.toggleHeatmap()
}

function onDetectStops(distanceThresholdM: number, timeThresholdMinutes: number) {
  store.fetchStopDetection(distanceThresholdM, timeThresholdMinutes)
}

function onToggleAnimation() {
  // Animation panel toggled, MapView will watch for animationData changes
}

function onAnalyzeCpa() {
  // CPA analysis triggered from LeftPanel
}
</script>

<template>
  <div class="flex flex-col h-screen dark">
    <TopNavBar />

    <div class="flex flex-1 overflow-hidden">
      <LeftPanel
        @query-track="onQueryTrack"
        @area-detect="onAreaDetect"
        @predict="onPredict"
        @calc-distance="onCalcDistance"
        @toggle-heatmap="onToggleHeatmap"
        @detect-stops="onDetectStops"
        @toggle-animation="onToggleAnimation"
        @analyze-cpa="onAnalyzeCpa"
      />
      <MapView ref="mapViewRef" />
      <RightPanel />
    </div>

    <StatusBar />

    <!-- Toast Notifications -->
    <div class="fixed top-16 right-4 z-[9999] space-y-2">
      <TransitionGroup name="toast">
        <div
          v-for="toast in store.toasts"
          :key="toast.id"
          class="rounded-lg shadow-xl px-4 py-3 flex items-center gap-3 min-w-[280px] border border-slate-600/50"
          style="background: #1e293b"
        >
          <div
            class="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0"
            :class="{
              'bg-emerald-500/20': toast.type === 'success',
              'bg-amber-500/20': toast.type === 'warning',
              'bg-ocean-500/20': toast.type === 'info',
              'bg-red-500/20': toast.type === 'error',
            }"
          >
            <!-- success -->
            <svg
              v-if="toast.type === 'success'"
              width="16" height="16" fill="none" stroke="#10B981" viewBox="0 0 24 24"
            >
              <polyline points="20 6 9 17 4 12" />
            </svg>
            <!-- warning -->
            <svg
              v-else-if="toast.type === 'warning'"
              width="16" height="16" fill="none" stroke="#F59E0B" viewBox="0 0 24 24"
            >
              <path
                d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"
              />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            <!-- info -->
            <svg
              v-else-if="toast.type === 'info'"
              width="16" height="16" fill="none" stroke="#0EA5E9" viewBox="0 0 24 24"
            >
              <circle cx="12" cy="12" r="10" />
              <path d="M12 16v-4" />
              <path d="M12 8h.01" />
            </svg>
            <!-- error -->
            <svg
              v-else
              width="16" height="16" fill="none" stroke="#EF4444" viewBox="0 0 24 24"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="15" y1="9" x2="9" y2="15" />
              <line x1="9" y1="9" x2="15" y2="15" />
            </svg>
          </div>
          <p class="text-sm text-white">{{ toast.message }}</p>
        </div>
      </TransitionGroup>
    </div>
  </div>
</template>
