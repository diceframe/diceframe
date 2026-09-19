import {
  createRouter,
  createWebHashHistory,
  type LocationQuery,
  type RouteLocationNormalized,
  type RouteRecordName,
  type RouteRecordRaw,
} from 'vue-router'
import { checkOwnerAccess, type OwnerAccessStatus } from '@/api/client'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: { name: 'overview' } },
  { path: '/login', name: 'login', component: () => import('@/features/auth/LoginView.vue') },
  { path: '/overview', name: 'overview', component: () => import('@/features/overview/OverviewView.vue') },
  { path: '/create', name: 'create', component: () => import('@/features/create/CreateView.vue') },
  { path: '/play', name: 'play', component: () => import('@/features/play/PlayView.vue') },
  { path: '/join', name: 'join', component: () => import('@/features/player/JoinView.vue') },
  { path: '/peer', name: 'peer', component: () => import('@/features/peer/PeerConnectView.vue') },
  { path: '/characters', name: 'characters', component: () => import('@/features/admin/CharactersView.vue') },
  { path: '/lorebook', name: 'lorebook', component: () => import('@/features/lorebook/LorebookView.vue') },
  { path: '/worlds', name: 'worlds', component: () => import('@/features/worlds/WorldsView.vue') },
  { path: '/adventures', name: 'adventures', component: () => import('@/features/admin/AdventuresView.vue') },
  { path: '/modules', name: 'modules', component: () => import('@/features/modules/ModulesView.vue') },
  { path: '/memory', name: 'memory', component: () => import('@/features/admin/MemoryView.vue') },
  { path: '/logs', name: 'logs', component: () => import('@/features/admin/LogsView.vue') },
  { path: '/rules', name: 'rules', component: () => import('@/features/admin/RulesView.vue') },
  { path: '/settings', name: 'settings', component: () => import('@/features/admin/SettingsView.vue') },
  { path: '/plugins', name: 'plugins', component: () => import('@/features/plugins/PluginsView.vue') },
  {
    path: '/legal/terms',
    name: 'legal-terms',
    component: () => import('@/features/legal/LegalView.vue'),
    props: { document: 'terms' },
  },
  {
    path: '/legal/privacy',
    name: 'legal-privacy',
    component: () => import('@/features/legal/LegalView.vue'),
    props: { document: 'privacy' },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

type PublicRoute = {
  name: RouteRecordName | null | undefined
  query: LocationQuery
}

export function isPublicRoute(route: PublicRoute): boolean {
  if (route.name === 'login' || route.name === 'join' || route.name === 'peer' || String(route.name || '').startsWith('legal-')) return true
  return route.name === 'play' && Boolean(route.query.user || route.query.share)
}

export async function requireOwnerAccess(
  to: RouteLocationNormalized,
  probe: () => Promise<OwnerAccessStatus> = checkOwnerAccess,
) {
  if (isPublicRoute(to)) return true
  const status = await probe()
  if (status !== 'login-required') return true
  return {
    name: 'login',
    query: { redirect: `${location.pathname}#${to.fullPath}` },
  }
}

router.beforeEach((to) => requireOwnerAccess(to))

export default router
