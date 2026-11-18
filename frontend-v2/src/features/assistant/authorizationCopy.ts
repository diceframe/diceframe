export function assistantAuthorizationCopy(locale: string) {
  const zh = locale.startsWith('zh')
  return {
    title: zh ? 'Agent 权限' : 'Agent access',
    hint: zh ? '选择这段会话可以操作的内容和方式，随时可以撤回。' : 'Choose what this conversation can change. You can revoke access at any time.',
    suggest: zh ? '建议' : 'Suggest',
    routine: zh ? '自动处理日常' : 'Handle routine work',
    task: zh ? '接管本次任务' : 'Take over this task',
    pendingApproval: zh ? '1 项待确认操作' : '1 action awaiting confirmation',
    reviewPending: zh ? '查看待确认操作' : 'Review pending action',
    continueTask: zh ? '继续任务' : 'Continue task',
    resumeTargets: zh ? '确认并继续剩余目标' : 'Confirm and resume remaining targets',
    resumeHint: zh ? '请核对剩余目标；已完成的目标不会重复执行。' : 'Review the remaining targets; completed targets will not be repeated.',
    continueHint: zh ? '继续会开启新一轮预算；已过期或用完的授权仍需重新授予。' : 'Continuing starts a new budget window; expired or exhausted access must be granted again.',
    revoke: zh ? '撤回，改为建议' : 'Revoke, use suggestions',
    current: zh ? '当前页面对象' : 'Current page object',
    currentUnavailable: zh ? '当前页面没有有效资源 ID；请明确选择“全部我的内容”。' : 'No valid page resource ID; choose All my content explicitly.',
    all: zh ? '全部我的内容' : 'All my content',
    steps: zh ? '步骤上限' : 'Step budget',
    usageUnknown: zh ? '部分用量未知' : 'some usage unknown',
    active: zh ? '已授权' : 'Authorized',
    mode: (value: string) => ({ suggest: zh ? '建议' : 'Suggest', routine: zh ? '自动处理日常' : 'Routine', task: zh ? '本次任务' : 'This task' }[value] || value),
    taskStatus: (value: string) => ({ pending: zh ? '等待中' : 'Pending', running: zh ? '执行中' : 'Running', completed: zh ? '已完成' : 'Completed', cancelled: zh ? '已取消' : 'Cancelled', failed: zh ? '失败' : 'Failed', partial: zh ? '部分完成' : 'Partially completed', paused: zh ? '已暂停' : 'Paused', approval_required: zh ? '等待授权' : 'Approval required', recovery_required: zh ? '结果待核实' : 'Outcome uncertain', interrupted: zh ? '已中断' : 'Interrupted', budget_reached: zh ? '已达到预算' : 'Budget reached' }[value] || value),
    disabled: zh ? '当前服务器不支持 Agent 授权控制。' : 'This server does not support Agent access controls.',
    error: zh ? '授权失败' : 'Authorization failed',
    effect: (mode: string) => mode === 'suggest'
      ? (zh ? '只分析、预览并逐项请求确认。' : 'Analyze and preview; ask before each change.')
      : mode === 'routine'
        ? (zh ? '在选定范围内执行常规修改，并保留结果。' : 'Run routine changes in the selected scope and retain results.')
        : (zh ? '在本次任务和步骤预算内连续执行。' : 'Continue this task within the selected scope and step budget.'),
  }
}
