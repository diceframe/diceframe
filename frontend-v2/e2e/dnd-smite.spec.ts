import { expect, test } from './fixtures'
import { accessToken } from './support'

test('paladin can cast Divine Smite after a hit using the free use and then a slot', async ({ page, request }, info) => {
  test.setTimeout(90_000)
  const headers = { Authorization: `Bearer ${accessToken()}`, 'X-TRPG-Confirm': 'true' }
  async function post(path: string, data: unknown) {
    const response = await request.post(path, { headers, data })
    const result = await response.json()
    expect(response.ok() && result.ok !== false, JSON.stringify(result)).toBe(true)
    return result
  }
  const draft: any = {
    locale: 'zh-CN', name: `至圣斩测试 ${info.project.name}`, level: 1,
    ability_method: 'standard_array', base_abilities: { str: 15, dex: 10, con: 13, int: 8, wis: 12, cha: 14 },
    class_ref: 'class:paladin', species_ref: 'species:human', background_ref: 'background:acolyte',
    class_skill_refs: ['skill:athletics', 'skill:intimidation'], class_tool_refs: [],
    equipment_package_ref: 'equipment_package:paladin_a', background_equipment_package_ref: 'equipment_package:acolyte_b',
    language_refs: ['language:common', 'language:dwarvish', 'language:elvish'], alignment: 'neutral',
    class_spell_choices: { cantrip_refs: [], spellbook_refs: [], prepared_spell_refs: ['spell:heroism', 'spell:cure_wounds'] },
  }
  const { choices } = await post('/api/rules/dnd2024_srd/builder/choices?language=zh-CN', draft)
  draft.background_ability_bonuses = { [choices.background_ability_refs[0].split(':')[1]]: 2, [choices.background_ability_refs[1].split(':')[1]]: 1 }
  draft.species_size = choices.species_sizes[0]
  draft.species_skill_refs = choices.species_skills.slice(0, choices.species_skill_count).map((row: any) => row.ref)
  draft.species_feat_refs = choices.species_feats.slice(0, choices.species_feat_count).map((row: any) => row.ref)
  draft.feat_choice_answers = Object.fromEntries(choices.feat_choices.map((feat: any) => [feat.feat_ref,
    Object.fromEntries(feat.specs.map((spec: any) => [spec.id, spec.options.slice(0, spec.count).map((row: any) => row.value)])),
  ]))
  const built = await post('/api/rules/dnd2024_srd/builder/finalize?language=zh-CN', draft)
  const advanced = await post('/api/rules/dnd2024_srd/advancement/apply?language=zh-CN', {
    character: built.character, choices: { class_spell_choices: { cantrip_refs: [], spellbook_refs: [], prepared_spell_refs: ['spell:heroism', 'spell:cure_wounds', 'spell:bless'] } },
  })
  const created = await post('/api/games/create', {
    world_id: 'default_fantasy', rule_id: 'dnd2024_srd', language: 'zh-CN', solo: true,
    game_name: `E2E Smite ${Date.now()}`, players: [advanced.character], play_mode: 'free',
  })
  const gameKey = String(created.game_key)
  const path = `/api/games/${encodeURIComponent(gameKey)}`
  try {
    const start = await post(`${path}/intents`, {
      intent_id: `smite-start-${info.project.name}`, type: 'combat.start', expected_version: 0,
      enemies: [{ id: 'training-target', name: '近战测试目标', creature_type: 'undead', hp: 200, armor_class: 1, speed: 0,
        position: 5, initiative_modifier: -20, attacks: [{ id: 'tap', name: '轻击', attack_bonus: -20, damage: '1d4', range: 5 }] }],
    })
    expect(start.gameplay.combat.status).toBe('active')
    await page.goto(`/#/play?game=${encodeURIComponent(gameKey)}`)
    await page.locator('[data-testid=dnd5e-campaign-tool]:visible').click()
    await page.locator('.dnd-toolbox-dialog').getByRole('button', { name: '战斗工具', exact: true }).click()
    await page.locator('[data-tile="spell"]').click()
    const spellCard = page.getByTestId('combat-spell-card')
    await expect(spellCard).toContainText('至圣斩')
    await expect(spellCard).toContainText('命中后可用')

    async function hit() {
      for (let attempt = 0; attempt < 5; attempt++) {
        await page.locator('[data-tile="attack"]').click()
        await page.getByRole('combobox', { name: '武器', exact: true }).selectOption('item:longsword')
        await page.locator('.dock-confirm').click()
        const finished = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/intents') && response.request().method() === 'POST', { timeout: 10_000 })
        await page.locator('.confirm-card .combat-primary').click()
        const result = await (await finished).json()
        expect(result.ok, JSON.stringify(result)).toBe(true)
        if (result.gameplay.combat.pending_decisions.some((row: any) => row.kind === 'melee_hit_spell')) return
        await page.getByRole('button', { name: '结束回合', exact: true }).click()
        await page.locator('.confirm-card .combat-primary').click()
      }
      throw new Error('Five attacks missed a target with AC 1')
    }
    async function cast(slot: number) {
      await spellCard.getByRole('combobox', { name: '法术位', exact: true }).selectOption(String(slot))
      await page.locator('.dock-confirm').click()
      await page.locator('.confirm-card .combat-primary').click()
      await expect(page.locator('.decision-card')).toHaveCount(0)
    }
    await hit()
    await expect(page.locator('.decision-card')).toContainText('不追加法术，结算攻击')
    await expect(spellCard).toContainText('免费施放（长休恢复）')
    expect(await spellCard.locator('select').last().locator('option').count()).toBe(1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
    await spellCard.screenshot({ path: info.outputPath('smite-hit.png') })
    await cast(0)
    const afterFree = await (await request.get(`${path}/characters`, { headers })).json()
    const paladin = afterFree.players.find((row: any) => row.character_sheet?.ruleset_character?.build?.class_levels?.[0]?.class_ref === 'class:paladin')
    expect(paladin.character_sheet.ruleset_character.resources.class.paladins_smite.current).toBe(0)
    expect(paladin.character_sheet.ruleset_character.spellcasting.class.slots_current['1']).toBe(2)
    await page.getByRole('button', { name: '结束回合', exact: true }).click()
    await page.locator('.confirm-card .combat-primary').click()
    await hit()
    await expect(spellCard.getByRole('combobox', { name: '法术位', exact: true }).locator('option[value="0"]')).toHaveCount(0)
    await cast(1)
    const afterSlot = await (await request.get(`${path}/characters`, { headers })).json()
    const updated = afterSlot.players.find((row: any) => row.user_id === paladin.user_id)
    expect(updated.character_sheet.ruleset_character.spellcasting.class.slots_current['1']).toBe(1)
    expect(updated.character_sheet.ruleset_character.resources.class.paladins_smite.current).toBe(0)
  } finally {
    const deleted = await request.delete(path, { headers })
    expect(deleted.ok(), `cleanup failed: ${await deleted.text()}`).toBe(true)
  }
})
