// W3 拍板 B：新建工程先问「要做什么」，按类型给可校验的模板工程。
// 模板必须能让 create_creator_project 的 source 直传，并通过预检
// （plugin.json 需要 schema_version/id/name/version/plugin_type）。
export type CreatorTemplateId = 'adventure' | 'rule' | 'gameplay' | 'toolkit'

export function templateSlug(name: string): string {
  const slug = name.trim().toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '')
  return slug || 'my-pack'
}

function readme(templateTitle: string, lines: string[]): string {
  return [`# ${templateTitle}`, '', ...lines, ''].join('\n')
}

export function templateSource(id: CreatorTemplateId, name: string): Record<string, string> {
  const slug = templateSlug(name)
  if (id === 'adventure') {
    return {
      'plugin.json': JSON.stringify({
        schema_version: 1,
        id: slug,
        name,
        version: '0.1.0',
        description: '',
        plugin_type: 'content-pack',
        content_profile: 'adventure-module',
        content_delivery_mode: 'catalog',
        config_schema: 'config.schema.json',
      }, null, 2),
      'config.schema.json': JSON.stringify({ type: 'object', properties: {} }, null, 2),
      'content/adventure.json': JSON.stringify({
        adventure_id: `plugin:${slug}`,
        version: '0.1.0',
        title: name,
        summary: '',
        chapters: [],
      }, null, 2),
      'README.md': readme(name, [
        '这是一个冒险模组工程的起点。',
        '',
        '- `content/adventure.json`：冒险主定义（章节、事件、冒险节点）。',
        '- 把 NPC/物品/地图放进 `content/` 下的对应子目录。',
        '- 编辑完成后点「预检」查看问题，然后「构建 .dfplugin」。',
        '- 想让 AI 帮忙？点右上角「AI 助手」，用一句话描述你想要的内容。',
      ]),
    }
  }
  if (id === 'rule') {
    return {
      'plugin.json': JSON.stringify({
        schema_version: 1,
        id: slug,
        name,
        version: '0.1.0',
        description: '',
        plugin_type: 'content-pack',
        config_schema: 'config.schema.json',
      }, null, 2),
      'config.schema.json': JSON.stringify({ type: 'object', properties: {} }, null, 2),
      'rules/rule.json': JSON.stringify({
        rule_id: slug,
        rule_name: name,
        description: '',
        dice_system: 'd20',
        combat_model: 'hp_based',
        attributes: [
          { key: 'str', name: '力量' },
          { key: 'dex', name: '敏捷' },
          { key: 'int', name: '智力' },
        ],
        attribute_points: 30,
      }, null, 2),
      'README.md': readme(name, [
        '这是一个规则模板工程的起点。',
        '',
        '- `rules/rule.json`：规则模板（属性、骰制、战斗模型）。',
        '- 需要自定义检定/结算逻辑时，`plugin_type` 要换成 `ruleset-runtime`，'
          + '并提供 RuntimeProbe stdio 进程（见 docs/plugins/EXTENDING_CN.md 的 Ruleset Runtime 章节）。',
        '- 也可以在「AI 助手」里描述你的规则构想，让 AI 先起草。',
      ]),
    }
  }
  if (id === 'gameplay') {
    return {
      'plugin.json': JSON.stringify({
        schema_version: 1,
        id: slug,
        name,
        version: '0.1.0',
        description: '',
        plugin_type: 'content-pack',
        config_schema: 'config.schema.json',
      }, null, 2),
      'config.schema.json': JSON.stringify({ type: 'object', properties: {} }, null, 2),
      'docs/gameplay-design.md': readme(name, [
        '玩法模式设计草稿。',
        '',
        '- 玩法（gameplay）决定一局怎么组织与推进：回合制、场景制、阶段制……',
        '- 完整玩法 provider 需要实现 TurnDriver/投影契约并在宿主注册；',
        '  先在这里把设计写清楚，再用「AI 助手」讨论实现路径。',
      ]),
      'README.md': readme(name, [
        '这是一个玩法工程的设计起点。把你的玩法规则、回合结构和界面设想写进 docs/。',
      ]),
    }
  }
  return {
    'plugin.json': JSON.stringify({
      schema_version: 1,
      id: slug,
      name,
      version: '0.1.0',
      description: '',
      plugin_type: 'tool',
      entrypoint: ['{python}', 'runtime/server.py'],
      permissions: ['tool.execute'],
      config_schema: 'config.schema.json',
    }, null, 2),
    'config.schema.json': JSON.stringify({ type: 'object', properties: {} }, null, 2),
    'runtime/server.py': [
      '# 工具插件入口骨架：使用 src.plugin_sdk.ToolRuntime 注册并执行工具。',
      '# 完整说明见 docs/plugins/EXTENDING_CN.md 的 Tool Plugin 章节。',
      '',
      'def main() -> None:',
      '    raise SystemExit("在此实现你的工具 runtime；这个骨架只占位。")',
      '',
      '',
      'if __name__ == "__main__":',
      '    main()',
      '',
    ].join('\n'),
    'README.md': readme(name, [
      '这是一个功能插件（工具）工程的起点。',
      '',
      '- `runtime/server.py`：JSON-RPC stdio 工具进程入口。',
      '- `plugin.json` 已声明 tool.execute 权限；宿主会在启动时校验 entrypoint。',
    ]),
  }
}
