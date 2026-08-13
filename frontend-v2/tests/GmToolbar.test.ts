import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { i18n } from '../src/i18n'
import GmToolbar from '../src/components/play/GmToolbar.vue'

describe('GmToolbar',()=>{
  it('emits one recap request and exposes its busy state',async()=>{
    i18n.global.locale.value = 'en'
    const wrapper=mount(GmToolbar,{global:{plugins:[i18n]},props:{
      detail:{game_key:'web|room|bot',round_number:4},
      players:[],
      isGm:true,
      recapBusy:false,
    }})

    const button=wrapper.get('button:nth-of-type(3)')
    expect(button.text()).toContain('Story Recap')
    await button.trigger('click')
    expect(wrapper.emitted('recap')).toHaveLength(1)

    await wrapper.setProps({recapBusy:true})
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toContain('Generating')
  })
})
