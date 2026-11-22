// GM 发起检定时可选的属性与技能。技能 id 与 content/skills/<id>.json 对应；
// 名称是面板自己的显示文案（内容侧的名称走 locale 文件，这里只服务下拉）。
export interface CheckOption {
  id: string
  zh: string
  en: string
}

export const ABILITY_OPTIONS: CheckOption[] = [
  { id: 'str', zh: '力量', en: 'Strength' },
  { id: 'dex', zh: '敏捷', en: 'Dexterity' },
  { id: 'con', zh: '体质', en: 'Constitution' },
  { id: 'int', zh: '智力', en: 'Intelligence' },
  { id: 'wis', zh: '感知', en: 'Wisdom' },
  { id: 'cha', zh: '魅力', en: 'Charisma' },
]

export const SKILL_OPTIONS: CheckOption[] = [
  { id: 'acrobatics', zh: '体操', en: 'Acrobatics' },
  { id: 'animal_handling', zh: '驯兽', en: 'Animal Handling' },
  { id: 'arcana', zh: '奥秘', en: 'Arcana' },
  { id: 'athletics', zh: '运动', en: 'Athletics' },
  { id: 'deception', zh: '欺瞒', en: 'Deception' },
  { id: 'history', zh: '历史', en: 'History' },
  { id: 'insight', zh: '洞悉', en: 'Insight' },
  { id: 'intimidation', zh: '威吓', en: 'Intimidation' },
  { id: 'investigation', zh: '调查', en: 'Investigation' },
  { id: 'medicine', zh: '医药', en: 'Medicine' },
  { id: 'nature', zh: '自然', en: 'Nature' },
  { id: 'perception', zh: '察觉', en: 'Perception' },
  { id: 'performance', zh: '表演', en: 'Performance' },
  { id: 'persuasion', zh: '说服', en: 'Persuasion' },
  { id: 'religion', zh: '宗教', en: 'Religion' },
  { id: 'sleight_of_hand', zh: '巧手', en: 'Sleight of Hand' },
  { id: 'stealth', zh: '隐匿', en: 'Stealth' },
  { id: 'survival', zh: '求生', en: 'Survival' },
]
