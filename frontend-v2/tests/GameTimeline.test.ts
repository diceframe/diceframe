import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { i18n } from '../src/i18n'
import GameTimeline from '../src/components/GameTimeline.vue'

describe('GameTimeline',()=>{
  it('shows shared gm narration and live player action',()=>{
    i18n.global.locale.value = 'zh-CN'
    const wrapper=mount(GameTimeline,{global:{plugins:[i18n]},props:{round:2,players:[{user_id:'p1',character_name:'艾琳'}],log:[{round:1,gm_response:'门缓缓打开。'}],live:[{user_id:'p1',text:'我检查门锁',revision_count:2}]}})
    expect(wrapper.text()).toContain('门缓缓打开')
    expect(wrapper.text()).toContain('艾琳 · 已公开 · 2/3')
    expect(wrapper.text()).toContain('我检查门锁')
  })

  it('renders a bounded recent window and reveals older rounds in batches',async()=>{
    i18n.global.locale.value = 'zh-CN'
    const log=Array.from({length:50},(_,index)=>({round:index+1,gm_response:`第${index+1}轮叙事`}))
    const wrapper=mount(GameTimeline,{global:{plugins:[i18n]},props:{round:50,players:[],log,live:[]}})

    expect(wrapper.text()).not.toContain('第30轮叙事')
    expect(wrapper.text()).toContain('第31轮叙事')
    expect(wrapper.text()).toContain('已收起更早的 30 轮')

    await wrapper.get('.timeline-history-gate button').trigger('click')
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
    await wrapper.get('.dice-tag-button').trigger('click')
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
})
