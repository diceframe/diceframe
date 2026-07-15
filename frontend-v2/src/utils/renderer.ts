function escapeHtml(s:unknown):string{
  return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')
}

function escapeRegExp(s:string):string{
  return s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')
}

const LIST_RE=/^([\-•*]|\d+[.、]|[一二三四五六七八九十]+[、.])\s*/
const CUE_RE=/^(?:随后|接着|与此同时|同时|忽然|突然|此时|检定|判定|结果|线索|任务|状态|奖励|代价|获得|失去|消耗|HP|生命|金币|经验|NPC|敌人|门|房间)/

function splitParagraphs(text:string):string[]{
  const source=String(text||'').replace(/\r\n/g,'\n').trim()
  if(!source)return []
  const blocks=source.split(/\n\s*\n/).map(s=>s.trim()).filter(Boolean)
  const out:string[]=[]
  for(const block of blocks){
    const normalized=block.replace(/([。！？；])\s*(?=(?:随后|接着|与此同时|同时|忽然|突然|此时|检定|判定|结果|线索|任务|状态|奖励|代价|获得|失去|消耗|HP|生命|金币|经验|NPC|敌人))/g,'$1\n')
    const lines=normalized.split(/\n+/).map(s=>s.trim()).filter(Boolean)
    const shouldSplit=lines.length>1&&(lines.some(line=>LIST_RE.test(line)||CUE_RE.test(line))||lines.length>=3||lines.every(line=>line.length<=90))
    if(shouldSplit)out.push(...lines)
    else out.push(lines.join('\n'))
  }
  return out
}

export interface StateCard{title:string;body:string;cls:'good'|'warn'}
function stateClass(title:string,body:string):'good'|'warn'{
  return /失败|警惕|受伤|扣除|失去|消耗|危险|倒地|中毒|拒绝|伤害|惩罚|代价|[－-]\s*\d+/.test(title+body)?'warn':'good'
}
const STATE_KEYWORDS='系统检定|任务更新|状态变化|状态变动|状态更新|玩家状态|资源变化|资源变动|资源更新|关系变化|关系变动|属性变化|属性变动|属性更新|线索更新|记忆更新|检定结果|战斗结算|奖励|代价|buff|debuff'
const STATE_TITLE_RE=new RegExp('^(?:'+STATE_KEYWORDS+')$','i')
const STATE_CUE_RE=/变化|变动|更新|结算|检定|奖励|代价/
function isStateTitle(title:string):boolean{return STATE_TITLE_RE.test(title)||STATE_CUE_RE.test(title)}
export function extractStateLines(text:string):{narration:string;states:StateCard[]}{
  const states:StateCard[]=[];const narration:string[]=[]
  String(text||'').replace(/\r\n/g,'\n').split(/\n+/).forEach(line=>{
    const t=line.trim()
    if(!t)return
    const bracket=t.match(/^【([^】]+)】\s*(.*)$/)
    if(bracket&&isStateTitle(bracket[1])){
      states.push({title:bracket[1],body:bracket[2]||'',cls:stateClass(bracket[1],bracket[2]||'')})
      return
    }
    const labeled=t.match(/^([^【】\[:：]+)[:：]\s*(.*)$/)
    if(labeled&&labeled[1].length<=12&&STATE_TITLE_RE.test(labeled[1])){
      states.push({title:labeled[1],body:labeled[2]||'',cls:stateClass(labeled[1],labeled[2]||'')})
      return
    }
    narration.push(line)
  })
  return {narration:narration.join('\n').trim(),states}
}

export interface Badge{cls:string;text:string}
export function formatTagLine(tagBlock:string):Badge[]{
  const badges:Badge[]=[]
  String(tagBlock||'').split('\n').forEach(raw=>{
    const line=raw.trim();if(!line)return
    const p=line.split(':');if(p.length<2)return
    const tag=p[0].toUpperCase();const uid=p[1]||'';const val=p.slice(2).join(':');const count=parseInt(val)
    if(tag==='HP'&&!isNaN(count))badges.push({cls:count<0?'hp-dn':'hp-up',text:'HP '+(count<0?String(count):'+'+count)})
    else if(tag==='GOLD'&&!isNaN(count))badges.push({cls:'gold',text:'金币 '+(count<0?String(count):'+'+count)})
    else if(tag==='PAY'&&!isNaN(count))badges.push({cls:'pay',text:'金币 '+(-Math.abs(count))})
    else if(tag==='LOOT'&&val)badges.push({cls:'loot',text:val})
    else if(tag==='KEY_ITEM'&&val)badges.push({cls:'loot',text:'🔑 '+val})
    else if(tag==='WEAPON'&&val)badges.push({cls:'loot',text:'⚔ '+val})
    else if(tag==='EQUIP'&&val)badges.push({cls:'loot',text:'🛡 '+val})
    else if(tag==='NPC'&&val)badges.push({cls:'npc',text:'NPC '+(val||uid)})
    else if(tag==='SCENE'&&val)badges.push({cls:'scene',text:val})
    else if(tag==='QUEST'&&val)badges.push({cls:'quest',text:val})
    else if(tag==='DECISION')badges.push({cls:'decision',text:val||'关键决策'})
    else if(tag==='XP'&&val)badges.push({cls:'gold',text:'XP +'+val})
    else if(tag==='ROLL'&&val)badges.push({cls:'roll',text:val})
  })
  return badges
}

export interface LoreKeywords{npc?:string[];location?:string[];item?:string[];faction?:string[];event?:string[];puzzle?:string[];other?:string[]}
interface Match{start:number;end:number;type:string;text:string}
const PRIORITY:Record<string,number>={roll:9,change:8,key:7,quote:6,event:5,npc:4,faction:3,puzzle:3,place:2,item:2,marker:2,other:1}
const TYPE_MAP:Record<string,string>={npc:'npc',location:'place',item:'item',faction:'faction',event:'event',puzzle:'puzzle',other:'other'}
const CSS_CLS:Record<string,string>={quote:'kw-quote',npc:'kw-npc',place:'kw-place',item:'kw-item',faction:'kw-faction',event:'kw-event',puzzle:'kw-puzzle',other:'kw-other',roll:'kw-roll',change:'kw-change',key:'kw-key',marker:'gm-list-marker'}

function addRegexMatches(raw:string,matches:Match[],type:string,re:RegExp){
  let m:RegExpExecArray|null
  while((m=re.exec(raw)))matches.push({start:m.index,end:m.index+m[0].length,type,text:m[0]})
}

export function highlightKeywords(text:string,lore?:LoreKeywords):string{
  const raw=String(text||'');const matches:Match[]=[]
  const kw=lore||{}
  let m:RegExpExecArray|null
  const marker=raw.match(LIST_RE);if(marker)matches.push({start:0,end:marker[0].length,type:'marker',text:marker[0]})
  addRegexMatches(raw,matches,'quote',/「([^」]+)」/g)
  addRegexMatches(raw,matches,'quote',/["“]([一-鿿]{2,20})["”]/g)
  addRegexMatches(raw,matches,'roll',/(?:D\d+|d\d+|\b\d+d\d+\b|掷骰|骰子|检定|判定|大成功|大失败|成功|失败|优势|劣势|DC\s*\d+|难度\s*\d+)/gi)
  addRegexMatches(raw,matches,'change',/(?:HP|生命|理智|魔力|资源|金币|金钱|经验|XP)\s*(?:[+-]\s*)?\d+|(?:获得|失去|消耗|扣除|回复|恢复|受伤|治疗)/g)
  addRegexMatches(raw,matches,'key',/(?:线索|任务|目标|关键|秘密|弱点|危险|警惕|陷阱|战斗|谜题|选择|决定|后果|代价|奖励|状态|公开行动|私密感知)/g)
  Object.keys(kw).forEach(t=>{
    const cls=TYPE_MAP[t]||'place'
    ;(kw[t as keyof LoreKeywords]||[]).forEach(name=>{
      if(!name||name.length<2)return
      const re=new RegExp(escapeRegExp(name),'g')
      while((m=re.exec(raw)))matches.push({start:m.index,end:m.index+name.length,type:cls,text:name})
    })
  })
  if(!kw.npc?.length&&!kw.location?.length&&!kw.item?.length){
    const npcSuf='(?:老板娘|老板|师傅|法师|牧师|盗贼|骑士|圣骑|游侠|德鲁伊|术士|猎人|长老|守卫|队长|团长|将领|佣兵|门卫|向导|商人|铁匠|药师|鉴定师|情报贩|村长|镇长|城主|国王|女王|王子|公主|贵族|乞丐|刺客)'
    const placeSuf='(?:镇|城|村|旅店|酒馆|公会|森林|遗迹|塔|庙|城堡|教堂|墓地|洞穴|广场|集市|港口|码头|要塞|宫殿|客栈|铁匠铺|药铺|学院|图书馆|山脉|河流|湖泊|沙漠|荒原|峡谷|平原)'
    const itemSuf='(?:徽章|剑|弓|法杖|盾|甲|袍|药剂|卷轴|宝石|钥匙|戒指|项链|披风|靴子|手套|头盔|背包|灯笼|火把|绳索|干粮|地图|令牌|魔石|水晶|短[刀剑]|长[刀剑弓枪矛]|巨[剑斧]|战[斧锤]|[左]?手枪|步枪|猎枪|冲锋枪|金币)'
    const reNPC=new RegExp('(^|[^\\u4e00-\\u9fff])([\\u4e00-\\u9fff]{1,3}'+npcSuf+')(?=[^\\u4e00-\\u9fff]|$)','g')
    while((m=reNPC.exec(raw)))matches.push({start:m.index+m[1].length,end:m.index+m[1].length+m[2].length,type:'npc',text:m[2]})
    const rePlace=new RegExp('(^|[^\\u4e00-\\u9fff])([\\u4e00-\\u9fff]{1,4}'+placeSuf+')(?=[^\\u4e00-\\u9fff]|$)','g')
    while((m=rePlace.exec(raw)))matches.push({start:m.index+m[1].length,end:m.index+m[1].length+m[2].length,type:'place',text:m[2]})
    const reItem=new RegExp('(^|[^\\u4e00-\\u9fff])([\\u4e00-\\u9fff]{1,4}'+itemSuf+')(?=[^\\u4e00-\\u9fff]|$)','g')
    while((m=reItem.exec(raw)))matches.push({start:m.index+m[1].length,end:m.index+m[1].length+m[2].length,type:'item',text:m[2]})
  }
  matches.sort((a,b)=>a.start!==b.start?a.start-b.start:(b.end-b.start)!==(a.end-a.start)?(b.end-b.start)-(a.end-a.start):(PRIORITY[b.type]||0)-(PRIORITY[a.type]||0))
  const clean:Match[]=[]
  for(const cur of matches){
    const oi=clean.findIndex(c=>cur.start<c.end&&cur.end>c.start)
    if(oi>=0){const c=clean[oi];if(cur.end-cur.start>c.end-c.start||(PRIORITY[cur.type]||0)>(PRIORITY[c.type]||0))clean[oi]=cur}
    else clean.push(cur)
  }
  clean.sort((a,b)=>a.start-b.start)
  let out='';let pos=0
  for(const c of clean){
    if(c.start<pos)continue
    out+=escapeHtml(raw.substring(pos,c.start))
    out+='<span class="'+(CSS_CLS[c.type]||'')+'">'+escapeHtml(raw.substring(c.start,c.end))+'</span>'
    pos=c.end
  }
  out+=escapeHtml(raw.substring(pos))
  return out
}

export interface GMBlock{paragraphs:string[];states:StateCard[];tags:Badge[]}
export function parseGMText(text:string,lore?:LoreKeywords):GMBlock{
  const extracted=extractStateLines(text)
  let narration=extracted.narration;let tagBlock=''
  const dash=narration.indexOf('---')
  if(dash>=0){tagBlock=narration.substring(dash+3).trim();narration=narration.substring(0,dash)}
  const paragraphs=splitParagraphs(narration).map(p=>highlightKeywords(p,lore))
  const states=extracted.states.map(s=>({title:s.title,cls:s.cls,body:highlightKeywords(s.body,lore)}))
  return {paragraphs,states,tags:formatTagLine(tagBlock)}
}