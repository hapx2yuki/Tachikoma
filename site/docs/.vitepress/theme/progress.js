// 端末ローカル (localStorage) のチェックリスト進捗
import { ref } from 'vue'
const KEY = 'tachikoma-progress-v1'
export const store = ref(load())
function load() {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') } catch { return {} }
}
export function save() {
  try { localStorage.setItem(KEY, JSON.stringify(store.value)) } catch {}
}
export function reload() { store.value = load() }
export function isChecked(step, i) { return !!(store.value[step] && store.value[step][i]) }
export function toggle(step, i) {
  const s = { ...(store.value[step] || {}) }
  s[i] = !s[i]
  store.value = { ...store.value, [step]: s }
  save()
}
export function countChecked(step) {
  const s = store.value[step] || {}
  return Object.values(s).filter(Boolean).length
}
export function resetStep(step) {
  const v = { ...store.value }; delete v[step]; store.value = v; save()
}
