import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
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
})
