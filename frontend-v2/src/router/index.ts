import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: { name: 'overview' } },
  { path: '/login', name: 'login', component: () => import('@/features/auth/LoginView.vue') },
  { path: '/overview', name: 'overview', component: () => import('@/features/overview/OverviewView.vue') },
  { path: '/create', name: 'create', component: () => import('@/features/create/CreateView.vue') },
  { path: '/play', name: 'play', component: () => import('@/features/play/PlayView.vue') },
  { path: '/join', name: 'join', component: () => import('@/features/player/JoinView.vue') },
  { path: '/characters', name: 'characters', component: () => import('@/features/admin/CharactersView.vue') },
  { path: '/lorebook', name: 'lorebook', component: () => import('@/features/admin/LorebookView.vue') },
  { path: '/memory', name: 'memory', component: () => import('@/features/admin/MemoryView.vue') },
  { path: '/logs', name: 'logs', component: () => import('@/features/admin/LogsView.vue') },
  { path: '/rules', name: 'rules', component: () => import('@/features/admin/RulesView.vue') },
  { path: '/settings', name: 'settings', component: () => import('@/features/admin/SettingsView.vue') },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
