const ROUTES_ZH: Record<string, string> = {
  overview: '总览', create: '创作', play: '游戏', characters: '角色', library: '内容库', creator: '创作台', map: '地图',
  lorebook: '世界书', worlds: '世界', adventures: '冒险', modules: '内容包', memory: '记忆', logs: '日志', rules: '规则',
  settings: '设置', plugins: '插件',
}
const ROUTES_EN: Record<string, string> = {
  overview: 'Overview', create: 'Create', play: 'Play', characters: 'Characters', library: 'Library', creator: 'Creator', map: 'Map',
  lorebook: 'Lorebook', worlds: 'Worlds', adventures: 'Adventures', modules: 'Content packages', memory: 'Memory', logs: 'Logs', rules: 'Rules',
  settings: 'Settings', plugins: 'Plugins',
}

export function assistantWorkspaceCopy(locale: string) {
  return locale.startsWith('zh') ? {
    expand: '展开任务工作区', collapse: '收起任务工作区', working: '处理中', attention: '需要处理',
    drafts: (count: number) => `${count} 份草稿`,
    resetPosition: '恢复悬浮球位置', resetPositionHint: '入口被移动到难找的位置时，可将它恢复到右下角。',
    goTo: (page: string) => `前往：${page}`, alreadyHere: (page: string) => `已在${page}页面`,
    opened: (page: string) => `已打开：${page}`, openAgain: '前往', back: '返回上一页', details: '详情',
    routes: ROUTES_ZH,
  } : {
    expand: 'Expand task workspace', collapse: 'Collapse task workspace', working: 'Working', attention: 'Needs attention',
    drafts: (count: number) => `${count} ${count === 1 ? 'draft' : 'drafts'}`,
    resetPosition: 'Reset launcher position', resetPositionHint: 'Return the launcher to the bottom right if it becomes difficult to find.',
    goTo: (page: string) => `Go to ${page}`, alreadyHere: (page: string) => `On ${page}`,
    opened: (page: string) => `Opened ${page}`, openAgain: 'Go there', back: 'Go back', details: 'Details',
    routes: ROUTES_EN,
  }
}
