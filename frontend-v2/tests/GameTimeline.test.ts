import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import GameTimeline from '../src/components/GameTimeline.vue'
import { playerColor } from '../src/utils/play'

describe('GameTimeline',()=>{
  it('keeps streamed paragraphs separate and displays markup as plain text', () => {
    const first = '你示意恩恩把提灯压低，沿着树根寻找通向旧码头的小径。'.repeat(5)
    const second = '韦恩举着灯，朝身后的调查员招手，远处传来船只靠岸的声音。'.repeat(5)
    const wrapper = mount(GameTimeline, {
      global: { plugins: [i18n] },
      props: { round: 12, players: [], log: [], live: [], processing: true,
        liveNarration: `${first}\n${second}<img src=x onerror=alert(1)>` },
    })
    const paragraphs = wrapper.findAll('.live-narration')
    expect(paragraphs).toHaveLength(2)
    expect(paragraphs[0]!.text()).toContain('你示意恩恩')
    expect(paragraphs[1]!.text()).toContain('韦恩举着灯')
    expect(paragraphs[1]!.text()).toContain('<img src=x onerror=alert(1)>')
    expect(wrapper.find('img').exists()).toBe(false)
    wrapper.unmount()
  })

  it('starts at the latest loaded round and preserves the reading position when new history arrives', async () => {
    i18n.global.locale.value = 'zh-CN'
    const wrapper = mount(GameTimeline, {
      global: { plugins: [i18n] },
      props: { gameKey: 'first', round: 2, players: [], live: [], log: [{ round: 1, gm_response: 'Loaded history' }] },
    })
    const timeline = wrapper.get('.timeline').element as HTMLElement
    const scrollTo = vi.fn((options: ScrollToOptions) => { timeline.scrollTop = options.top || 0 })
    Object.defineProperties(timeline, {
      scrollTo: { configurable: true, value: scrollTo },
      scrollHeight: { configurable: true, value: 1600 },
      clientHeight: { configurable: true, value: 400 },
    })
    await flushPromises()
    expect(timeline.scrollTop).toBe(1600)
    timeline.scrollTop = 300
    await wrapper.get('.timeline').trigger('scroll')
    scrollTo.mockClear()
    await wrapper.setProps({ log: [{ round: 1, gm_response: 'Loaded history' }, { round: 2, gm_response: 'New history' }] })
    await flushPromises()
    expect(scrollTo).not.toHaveBeenCalled()
    expect(timeline.scrollTop).toBe(300)
    expect(wrapper.get('.new-message').text()).toContain('新消息')
    await wrapper.setProps({ gameKey: 'second' })
    await flushPromises()
    expect(timeline.scrollTop).toBe(1600)
    wrapper.unmount()
  })

  it('shows shared gm narration and live player action',()=>{
    i18n.global.locale.value = 'zh-CN'
    const wrapper=mount(GameTimeline,{global:{plugins:[i18n]},props:{round:2,players:[{user_id:'p1',character_name:'艾琳'}],log:[{round:1,gm_response:'门缓缓打开。'}],live:[{user_id:'p1',text:'我检查门锁',revision_count:2}]}})
    expect(wrapper.text()).toContain('门缓缓打开')
    expect(wrapper.text()).toContain('艾琳 · 已公开 · 2/3')
    expect(wrapper.text()).toContain('我检查门锁')
  })

  it('shares stable speaker colour across history and live without colouring card edges or the GM', () => {
    const wrapper = mount(GameTimeline, {
      global: { plugins: [i18n] },
      props: {
        round: 2,
        players: [{ user_id: 'p1', character_name: '艾琳' }, { user_id: 'p2', character_name: '林恩' }],
        log: [{ round: 1, actions: [{ user_id: 'p1', text: '检查门锁' }, { user_id: 'p2', text: '观察窗户' }], gm_response: '门后传来脚步声。' }],
        live: [{ user_id: 'p1', character_name: '艾琳·KeeperOfTheAncientNorthernArchive', text: '推开门' }],
      },
    })
    const cards = wrapper.findAll('.message.player')
    expect(cards).toHaveLength(3)
    for (const [index, card] of cards.entries()) {
      const uid = index === 1 ? 'p2' : 'p1'
      const style = (card.element as HTMLElement).style
      expect(style.borderLeftColor).toBe('')
      expect(style.borderRightColor).toBe('')
      expect(style.getPropertyValue('--speaker-color')).toBe(playerColor(uid))
      const speaker = card.get('.message-speaker-name')
      expect(speaker.text()).toContain(index === 1 ? '林恩' : '艾琳')
      expect(card.find('.portrait-image').exists()).toBe(true)
    }
    expect(cards[2]!.get('.message-speaker-name').text()).toBe('艾琳·KeeperOfTheAncientNorthernArchive')
    const gm = wrapper.get('.message.gm')
    expect((gm.element as HTMLElement).style.getPropertyValue('--speaker-color')).toBe('')
    expect(gm.get('.narrator-avatar').text()).toBe('GM')
    expect(gm.find('.message-speaker-name').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders a bounded recent window and reveals older rounds in batches',async()=>{
    i18n.global.locale.value = 'zh-CN'
    const log=Array.from({length:50},(_,index)=>({round:index+1,gm_response:`第${index+1}轮叙事`}))
    const wrapper=mount(GameTimeline,{global:{plugins:[i18n]},props:{round:50,players:[],log,live:[]}})

    expect(wrapper.text()).not.toContain('第30轮叙事')
    expect(wrapper.text()).toContain('第31轮叙事')
    expect(wrapper.text()).toContain('已收起更早的 30 轮')

    const revealOlder = wrapper.findAll('button').find(button => button.text().includes('再看前'))
    expect(revealOlder).toBeDefined()
    await revealOlder!.trigger('click')
    expect(wrapper.text()).toContain('第11轮叙事')
    expect(wrapper.text()).toContain('已收起更早的 10 轮')
  })

  it('renders structured check results and hides internal system actions',()=>{
    i18n.global.locale.value = 'zh-CN'
    const wrapper=mount(GameTimeline,{global:{plugins:[i18n]},props:{
      round:2,
      players:[{user_id:'p1',character_name:'艾琳'}],
      log:[{
        round:1,
        actions:[
          {user_id:'system',text:'【GM指令】隐藏这条内容'},
          {user_id:'p1',text:'悄悄上楼'},
        ],
        gm_response:'木板发出脆响。',
        check_results:[{
          check_id:'check-1',
          label:'潜行检定',
          actor_uid:'p1',
          actor_name:'艾琳',
          dice:'d100',
          roll:54,
          threshold:20,
          hard_threshold:10,
          extreme_threshold:4,
          verdict:'失败',
        }],
      }],
      live:[],
    }})

    expect(wrapper.text()).toContain('潜行检定 · 艾琳')
    expect(wrapper.text()).toContain('d100=54 / 20')
    expect(wrapper.text()).toContain('失败')
    expect(wrapper.text()).not.toContain('GM指令')
  })

  it('renders manual roll results in the shared timeline',()=>{
    i18n.global.locale.value = 'zh-CN'
    const wrapper=mount(GameTimeline,{global:{plugins:[i18n]},props:{
      round:2, players:[{user_id:'p1',character_name:'艾琳'}],
      log:[{round:1,gm_response:'门缓缓打开。'}], live:[],
      manualRolls:[{
        id:'roll-1', round_number:1, label:'察觉鉴定', formula:'d20', status:'resolved',
        target_names:{p1:'艾琳'}, results:{p1:{total:15,rolls:[15],modifier:0,natural:15}},
      }],
    }})

    const card=wrapper.get('.manual-roll-timeline-card')
    expect(card.text()).toContain('手动投掷 · 察觉鉴定')
    expect(card.text()).toContain('察觉鉴定 · d20')
    expect(card.text()).toContain('艾琳：总值 15（自然 15）')
  })

  it('offers the check owner a direct Luck decision before narration',async()=>{
    vi.useFakeTimers()
    i18n.global.locale.value = 'zh-CN'
    const check = {
      check_id:'luck-1', actor_uid:'p1', actor_name:'艾琳', label:'考古学检定',
      dice:'d100', roll:52, threshold:50, verdict:'失败', luck_cost:2,
      luck_spend_available:true, luck_decision:'pending',
    }
    const wrapper=mount(GameTimeline,{global:{plugins:[i18n]},props:{
      round:2,players:[{user_id:'p1',character_name:'艾琳'}],log:[],live:[],
      pendingChecks:[check],currentUserId:'p1',
    }})

    await vi.advanceTimersByTimeAsync(720)
    expect(wrapper.text()).toContain('消耗 2 点幸运 → 普通成功')
    expect(wrapper.text()).toContain('保留失败')
    const spendLuck = wrapper.findAll('button').find(button => button.text().includes('消耗 2 点幸运'))
    expect(spendLuck).toBeDefined()
    await spendLuck!.trigger('click')
    expect(wrapper.emitted('luck')?.[0]).toEqual([check,true])
    vi.useRealTimers()
  })

  it('renders a public story recap as plain text after its round',()=>{
    i18n.global.locale.value = 'en'
    const wrapper=mount(GameTimeline,{global:{plugins:[i18n]},props:{
      round:4,
      players:[],
      log:[{
        round:4,
        gm_response:'The door closes behind the party.',
        story_recaps:[{
          id:'recap-1',
          text:'The party reached the archive. <b>This stays plain text.</b>',
          from_round:1,
          to_round:4,
        }],
      }],
      live:[],
    }})

    const card=wrapper.get('[data-testid="story-recap-card"]')
    expect(card.text()).toContain('Story Recap')
    expect(card.text()).toContain('Rounds 1–4')
    expect(card.text()).toContain('<b>This stays plain text.</b>')
    expect(card.find('b').exists()).toBe(false)
  })

  it('renders generated scene images through the game-scoped component',()=>{
    const wrapper=mount(GameTimeline,{global:{plugins:[i18n],stubs:{SceneImageBlock:{
      props:['assetId','gameKey'],
      template:'<div data-testid="generated-scene" :data-asset-id="assetId" :data-game-key="gameKey" />',
    }}},props:{
      gameKey:'web|room|bot',round:2,players:[],live:[],log:[{
        round:1,
        gm_response:'The harbor emerges from the fog.',
        scene_image:{
          status:'ready',
          prompt:'misty harbor',
          reference:{kind:'generated',asset_id:'asset-1'},
        },
      }],
    }})

    const image=wrapper.get('[data-testid="generated-scene"]')
    expect(image.attributes('data-asset-id')).toBe('asset-1')
    expect(image.attributes('data-game-key')).toBe('web|room|bot')
  })

  it('disables only the reveal currently being saved, in history and the current round', async () => {
    i18n.global.locale.value = 'zh-CN'
    const history = { check_id: 'history', actor_uid: 'gm', roll: 45, dice: 'd100' }
    const current = { check_id: 'current', actor_uid: 'gm', roll: 65, dice: 'd100' }
    const wrapper = mount(GameTimeline, {
      global: { plugins: [i18n] },
      props: { gameKey: 'web|room|bot', round: 2, players: [], live: [],
        log: [{ round: 1, check_results: [history] }], revealChecks: [current],
        isGm: true, diceRevealMode: 'click', revealBusyIds: ['current'] },
    })
    const buttons = () => wrapper.findAll('.check-reveal-actions button')
    expect(buttons()).toHaveLength(2)
    expect(buttons()[0]!.attributes('disabled')).toBeUndefined()
    expect(buttons()[1]!.attributes('disabled')).toBeDefined()
    await wrapper.setProps({ revealBusyIds: ['history'] })
    expect(buttons()[0]!.attributes('disabled')).toBeDefined()
    expect(buttons()[1]!.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })
})
