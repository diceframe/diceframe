// W3 拍板 B：新建工程先问「要做什么」，按类型给可校验的模板工程。
// 模板必须能让 create_creator_project 的 source 直传，并通过预检
// （plugin.json 需要 schema_version/id/name/version/plugin_type）。
export type CreatorTemplateId = 'adventure' | 'rule' | 'gameplay' | 'toolkit'

export function templateSlug(name: string): string {
  const raw = name.trim()
  const slug = raw.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '')
  const codeSlug = Array.from(raw)
    .filter(character => (character.codePointAt(0) || 0) > 0x7f)
    .map(character => character.codePointAt(0)?.toString(16) || '')
    .filter(Boolean)
    .join('-')
  if (slug && !codeSlug) return slug.slice(0, 56)
  if (slug && codeSlug) return `${slug}-${codeSlug}`.slice(0, 56)
  return codeSlug ? `pack-${codeSlug}`.slice(0, 56) : 'my-pack'
}

function readme(templateTitle: string, lines: string[]): string {
  return [`# ${templateTitle}`, '', ...lines, ''].join('\n')
}

export function templateSource(id: CreatorTemplateId, name: string): Record<string, string> {
  const slug = templateSlug(name)
  if (id === 'adventure') {
    const adventureRoot = `adventures/${slug}`
    return {
      'plugin.json': JSON.stringify({
        schema_version: 1,
        content_schema_version: 2,
        locale_schema_version: 1,
        default_locale: 'zh-CN',
        id: slug,
        name,
        version: '0.1.0',
        description: '',
        plugin_type: 'content-pack',
        content_profile: 'adventure-module',
        content_delivery_mode: 'catalog',
        adventure_packages: [adventureRoot],
        config_schema: 'config.schema.json',
        contributes: {},
      }, null, 2),
      'config.schema.json': JSON.stringify({ type: 'object', properties: {} }, null, 2),
      [`${adventureRoot}/manifest.json`]: JSON.stringify({
        schema_version: 1,
        adventure_id: `plugin:${slug}`,
        version: '0.1.0',
        format: 'diceframe:adventure-graph-v2',
        world_policy: 'portable',
        required_runtime: { id: 'core:dnd2024', minimum_version: 1 },
        default_locale: 'zh-CN',
        supported_locales: ['zh-CN'],
      }, null, 2),
      [`${adventureRoot}/adventure.json`]: JSON.stringify({
        schema_version: 2,
        kind: 'adventure',
        id: slug,
        format: 'diceframe:adventure-graph-v2',
        source_ref: `creator:${slug}`,
        automation_level: 'guided',
        name,
        summary: '',
        chapters: [],
        objectives: [],
        milestones: [],
        start_node_ids: ['start'],
        nodes: [{
          id: 'start',
          type: 'scene',
          name: '开场',
          description: '在这里写下冒险的开场场景。',
          transitions: [],
        }],
      }, null, 2),
      'README.md': readme(name, [
        '这是一个符合当前 DiceFrame 冒险目录契约的内容包工程。',
        '',
        `- \`${adventureRoot}/adventure.json\`：冒险主定义（章节、事件、冒险节点）。`,
        `- \`${adventureRoot}/manifest.json\`：运行时、版本和语言声明。`,
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
        capabilities: ['content.rule'],
        permissions: ['content.read', 'content.import'],
        contributes: { rules: ['content/rules/*.json'] },
      }, null, 2),
      'config.schema.json': JSON.stringify({ type: 'object', properties: {} }, null, 2),
      [`content/rules/${slug}.json`]: JSON.stringify({
        rule_id: slug,
        rule_name: name,
        description: '',
        dice_system: 'd20',
        combat_model: 'hp_based',
        check_mechanic: {
          dice: 'd20',
          comparison: 'roll_plus_modifier_gte_target',
          critical: { success: 20, failure: 1 },
        },
        max_check_dc: 20,
        hp_formula: '10 + CON',
        attributes: [
          { key: 'str', name: '力量', min: 1, max: 20 },
          { key: 'dex', name: '敏捷', min: 1, max: 20 },
          { key: 'con', name: '体质', min: 1, max: 20 },
          { key: 'int', name: '智力', min: 1, max: 20 },
          { key: 'wis', name: '感知', min: 1, max: 20 },
          { key: 'cha', name: '魅力', min: 1, max: 20 },
        ],
        skills: [],
        attribute_points: 60,
      }, null, 2),
      'README.md': readme(name, [
        '这是一个可安装的规则配置内容包工程。',
        '',
        `- \`content/rules/${slug}.json\`：规则配置（属性、骰制、战斗模型和检定方式）。`,
        '- 需要自定义检定/结算逻辑时，`plugin_type` 要换成 `ruleset-runtime`，'
          + '并提供 RuntimeProbe stdio 进程（见 docs/plugins/EXTENDING_CN.md 的 Ruleset Runtime 章节）。',
        '- 也可以在「AI 助手」里描述你的规则构想，让 AI 直接修改这个规则文件。',
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
        '这是一个玩法方案工程的设计起点，不会直接注册成可运行玩法。把你的玩法规则、回合结构和界面设想写进 docs/。',
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
      capabilities: ['tool.roll_dice'],
      permissions: ['process.spawn', 'plugin.config', 'plugin.data', 'tool.execute'],
      config_schema: 'config.schema.json',
    }, null, 2),
    'config.schema.json': JSON.stringify({ type: 'object', properties: {} }, null, 2),
    'runtime/server.py': [
      '"""可直接运行的 DiceFrame 骰子助手示例。"""',
      'from __future__ import annotations',
      '',
      'import random',
      'import re',
      'from src.plugin_sdk import ToolRuntime',
      '',
      'runtime = ToolRuntime()',
      '',
      '@runtime.tool(',
      '    name="roll_dice",',
      '    title="掷骰子",',
      '    description="按 NdM+K 形式掷骰，例如 2d6+1。",',
      '    input_schema={',
      '        "type": "object",',
      '        "properties": {"notation": {"type": "string", "description": "NdM+K"}},',
      '        "required": ["notation"],',
      '        "additionalProperties": False,',
      '    },',
      ')',
      'def roll_dice(arguments: dict, context: dict) -> dict:',
      '    notation = str(arguments.get("notation") or "").strip().lower()',
      '    match = re.fullmatch(r"(\\d+)d(\\d+)([+-]\\d+)?", notation)',
      '    if not match:',
      '        raise ValueError("骰式必须是 NdM 或 NdM+K，例如 2d6+1")',
      '    count, sides = int(match.group(1)), int(match.group(2))',
      '    modifier = int(match.group(3) or 0)',
      '    if not 1 <= count <= 100 or not 2 <= sides <= 1000:',
      '        raise ValueError("骰子数量或面数超出范围")',
      '    rolls = [random.randint(1, sides) for _ in range(count)]',
      '    total = sum(rolls) + modifier',
      '    return {"content": [{"type": "text", "text": f"{notation} = {total}（{rolls}，修正 {modifier:+d}）"}], "data": {"rolls": rolls, "modifier": modifier, "total": total}}',
      '',
      'if __name__ == "__main__":',
      '    runtime.run()',
      '',
    ].join('\n'),
    'README.md': readme(name, [
        '这是一个可以直接构建、安装并调用的工具插件工程。',
      '',
        '- `runtime/server.py`：已经实现 `roll_dice` 的 JSON-RPC stdio 工具进程入口。',
      '- `plugin.json` 已声明 tool.execute 权限；宿主会在启动时校验 entrypoint。',
        '- 在「AI 助手」里可以继续让 AI 增加查询器、导入器或其他工具。',
    ]),
  }
}
