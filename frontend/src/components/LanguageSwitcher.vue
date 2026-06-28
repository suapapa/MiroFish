<template>
  <div class="language-switcher" ref="switcherRef">
    <button
      type="button"
      class="switcher-trigger"
      :class="variant"
      :aria-label="$t('a11y.languageSwitcher')"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggleDropdown"
      @keydown.escape="closeDropdown"
    >
      {{ currentLabel }}
      <span class="caret" aria-hidden="true">{{ open ? '▲' : '▼' }}</span>
    </button>
    <ul
      v-if="open"
      class="switcher-dropdown"
      role="listbox"
      :aria-label="$t('a11y.languageSwitcher')"
    >
      <li
        v-for="loc in availableLocales"
        :key="loc.key"
        role="option"
        :aria-selected="loc.key === locale"
      >
        <button
          type="button"
          class="switcher-option"
          :class="{ active: loc.key === locale }"
          @click="switchLocale(loc.key)"
        >
          {{ loc.label }}
        </button>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { availableLocales } from '@/i18n/index.js'

const props = defineProps({
  variant: {
    type: String,
    default: 'light',
    validator: (v) => ['light', 'dark'].includes(v)
  }
})

const { locale } = useI18n()
const open = ref(false)
const switcherRef = ref(null)

const currentLabel = computed(() => {
  const found = availableLocales.find(l => l.key === locale.value)
  return found ? found.label : locale.value
})

const toggleDropdown = () => {
  open.value = !open.value
}

const closeDropdown = () => {
  open.value = false
}

const switchLocale = (key) => {
  locale.value = key
  localStorage.setItem('locale', key)
  document.documentElement.lang = key
  open.value = false
}

const onClickOutside = (e) => {
  if (switcherRef.value && !switcherRef.value.contains(e.target)) {
    open.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', onClickOutside)
  document.documentElement.lang = locale.value
})

onUnmounted(() => {
  document.removeEventListener('click', onClickOutside)
})
</script>

<style scoped>
.language-switcher {
  position: relative;
  display: inline-block;
  font-family: var(--mf-font-mono);
}

.switcher-trigger {
  background: transparent;
  border: 1px solid var(--mf-border-strong);
  padding: 8px 12px;
  min-height: var(--mf-touch-min);
  font-family: var(--mf-font-mono);
  font-size: 0.8rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: border-color var(--mf-duration-fast), opacity var(--mf-duration-fast);
}

.switcher-trigger.light {
  color: var(--mf-gray-500);
}

.switcher-trigger.light:hover {
  border-color: var(--mf-gray-400);
}

.switcher-trigger.dark {
  color: var(--mf-white);
  border-color: rgba(255, 255, 255, 0.45);
}

.switcher-trigger.dark:hover {
  border-color: var(--mf-white);
}

.caret {
  font-size: 0.6rem;
}

.switcher-dropdown {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 4px;
  background: var(--mf-white);
  border: 1px solid var(--mf-gray-300);
  list-style: none;
  padding: 4px 0;
  min-width: 100%;
  z-index: var(--mf-z-dropdown);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.switcher-option {
  display: block;
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  padding: 10px 12px;
  min-height: var(--mf-touch-min);
  font-size: 0.8rem;
  color: var(--mf-gray-600);
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--mf-duration-fast);
}

.switcher-option:hover {
  background: var(--mf-gray-100);
}

.switcher-option.active {
  color: var(--mf-orange);
  font-weight: 600;
}
</style>
