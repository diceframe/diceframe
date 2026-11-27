import { describe, expect, it } from 'vitest'

import { checkVerdictLabel, isSuccessVerdict, verdictCodeOf, verdictLabel } from '@/utils/checkVerdict'

describe('check verdict codes', () => {
  it('reads only known codes and never guesses from the label', () => {
    expect(verdictCodeOf({ verdict_code: 'hard_success' })).toBe('hard_success')
    expect(verdictCodeOf({ verdict_code: 'HARD_SUCCESS' })).toBe('hard_success')
    expect(verdictCodeOf({ verdict_code: '困难成功' })).toBeUndefined()
    expect(verdictCodeOf({ verdict_code: 'whatever' })).toBeUndefined()
    expect(verdictCodeOf(undefined)).toBeUndefined()
  })

  it('classifies success from the code ladder', () => {
    expect(isSuccessVerdict('success')).toBe(true)
    expect(isSuccessVerdict('regular_success')).toBe(true)
    expect(isSuccessVerdict('critical_success')).toBe(true)
    expect(isSuccessVerdict('failure')).toBe(false)
    expect(isSuccessVerdict('critical_failure')).toBe(false)
    expect(isSuccessVerdict(undefined)).toBe(false)
  })

  it('labels codes and falls back to the frozen server text', () => {
    expect(verdictLabel('critical_failure')).toBeTruthy()
    expect(verdictLabel('not_a_code')).toBe('')
    // 旧存档 / 自定义 runtime：没有 code 时显示后端冻结的文本
    expect(checkVerdictLabel({ verdict: 'Barely a success' })).toBe('Barely a success')
    expect(checkVerdictLabel({ verdict_code: 'failure', verdict: '失败' })).toBe(verdictLabel('failure'))
  })
})