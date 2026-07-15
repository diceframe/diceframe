<script setup lang="ts">
import { NCollapse, NCollapseItem } from 'naive-ui'
</script>

<template>
  <NCollapse :default-expanded-names="['guide']" class="napcat-guide" arrow-placement="right">
    <NCollapseItem title="使用说明（点我折叠 / 展开）" name="guide">
      <section>
        <h4>这是什么</h4>
        <p>通过 NapCat 把群聊连到 DiceFrame。玩家在群聊里 <code>@bot</code> 发消息，就像在网页游玩页一样推进剧情、掷骰、查状态。</p>
      </section>

      <section>
        <h4>1. 先装 NapCat</h4>
        <p>NapCat 是 QQ 机器人协议端（OneBot 11）。装好后，进入 <strong>网络设置 → 网络配置 → WebSocket 服务器</strong>，启用后记下两个值：</p>
        <ul>
          <li><strong>端口</strong>（默认 3001）</li>
          <li><strong>access_token</strong>（访问令牌，自己设一个）</li>
        </ul>
      </section>

      <section>
        <h4>2. 在这里填对应值</h4>
        <p>把 NapCat 那边的值填到下方表单：</p>
        <ul>
          <li><strong>主机地址</strong> = NapCat 所在机器地址（NapCat 跟 DiceFrame 同机填 <code>127.0.0.1</code>）</li>
          <li><strong>端口</strong> = NapCat 的 WebSocket 服务器端口（如 3001）</li>
          <li><strong>访问令牌</strong> = NapCat 的 access_token</li>
        </ul>
        <p>填完点 <strong>保存配置</strong>，再点 <strong>重启插件</strong>。状态变成 <code>running</code> 就连上了。</p>
      </section>

      <section>
        <h4>3. 名单过滤（可选）</h4>
        <p><strong>启用聊天名单过滤</strong>：</p>
        <ul>
          <li>关 = 任意群 / 私聊 <code>@bot</code> 都响应</li>
          <li>开 = 只响应下方"群聊名单""私聊名单"里的目标（白名单）</li>
        </ul>
        <p class="hint">建议开了过滤、填自己的群号，避免 bot 在不相关的群里乱回。</p>
      </section>

      <section>
        <h4>4. 房主开团</h4>
        <ol>
          <li>在 DiceFrame 创建游戏</li>
          <li>进入游玩页，右侧 <strong>GM 操作</strong> 侧栏点 <strong>"一次性 Bot 绑定"</strong> 按钮（每点一次都会生成新凭证、让旧凭证作废，并复制 <code>绑定 &lt;game_key&gt; &lt;一次性凭证&gt;</code> 到剪贴板）</li>
          <li>到群聊里 <strong>@bot</strong> 粘贴发送这条绑定指令</li>
          <li>bot 回复"已绑定《游戏名》"即开团成功；该凭证会立即作废，重绑请回网页重新复制</li>
        </ol>
        <p class="hint">不要把历史绑定命令转发给玩家；如果误发，绑定成功后旧命令也不能再抢 GM 身份。</p>
      </section>

      <section>
        <h4>5. 邀请玩家 + 指令速查</h4>
        <p>让玩家先在群聊里 <strong>@bot</strong> 发 <code>加入 角色名</code> 认领角色，认领后就能 @bot 玩了。中途进群可以先发 <code>@bot 前情</code> 看公开回顾，或发 <code>@bot 地图</code> 看当前地点；没有角色就发 <code>@bot 新建角色</code> 或 <code>@bot 车卡</code> 拿填写提示和网页入口。若开启 AI 辅助车卡，bot 会主动私聊玩家收集自然语言描述，确认后把公开版草稿发回群里。发送 <code>@bot 邀请</code> 时，bot 会同时发邀请链接和一张新玩家一图流教程卡。</p>
        <table class="cmd-table">
          <thead><tr><th>指令</th><th>作用</th><th>谁可用</th></tr></thead>
          <tbody>
            <tr><td><code>@bot 帮助</code></td><td>看指令列表</td><td>所有人</td></tr>
            <tr><td><code>@bot 前情</code></td><td>查看公开前情提要和最近回合</td><td>所有人</td></tr>
            <tr><td><code>@bot 地图</code></td><td>查看当前场景和地点连接</td><td>所有人</td></tr>
            <tr><td><code>@bot 邀请</code></td><td>发送当前局网页加入链接</td><td>所有人</td></tr>
            <tr><td><code>@bot 新建角色 / 车卡</code></td><td>发送车卡建议；可主动私聊 AI 辅助草稿</td><td>所有人</td></tr>
            <tr><td><code>@bot 加入 角色名</code></td><td>认领角色</td><td>玩家</td></tr>
            <tr><td><code>@bot 绑定 &lt;key&gt; &lt;一次性凭证&gt;</code></td><td>绑定游戏到群聊，成功后凭证作废</td><td>仅 GM</td></tr>
            <tr><td><code>@bot 状态</code></td><td>查角色 HP / 金币 / 背包</td><td>已认领角色者</td></tr>
            <tr><td><code>@bot 感知</code></td><td>私聊发送最近的角色专属感知</td><td>已认领角色者</td></tr>
            <tr><td><code>@bot 支付</code></td><td>私聊发送待确认支付列表</td><td>已认领角色者</td></tr>
            <tr><td><code>@bot 确认支付 / 拒绝支付</code></td><td>处理待确认付款</td><td>已认领角色者</td></tr>
            <tr><td><code>@bot 掷骰</code></td><td>确认需要骰子的行动</td><td>已认领角色者</td></tr>
            <tr><td><code>@bot 推进 / 下一轮</code></td><td>强制推进当前回合</td><td>GM 或已授权账号</td></tr>
            <tr><td><code>@bot 暂离 / 回来</code></td><td>临时下线不阻塞回合；回来后继续行动</td><td>已认领角色者 / GM</td></tr>
            <tr><td><code>@bot &lt;自然语言行动&gt;</code></td><td>描述行动，AI 推进剧情</td><td>已认领角色者</td></tr>
          </tbody>
        </table>
        <p class="hint">每轮最多修改行动 3 次，AI 只读取最后一版；如果这条行动会触发 GM 生成，bot 会先提示“GM 正在思考中”。推进权限默认只有绑定本局的 GM，可在配置里的“可推进的账号 ID”额外添加。</p>
      </section>

      <section>
        <h4>6. 卡片缓存</h4>
        <p>Bot 发送帮助、状态、车卡教程等图片时，会在本机 <code>data/bot/cards</code> 临时保存 PNG。插件会按“卡片缓存保留时长 / 最多保留张数”自动清理；也可以在本页点 <strong>清理卡片缓存</strong> 立即删除临时卡片。</p>
        <p class="hint">清理只会处理自动生成的 <code>card_*.png</code>，不会删除游戏存档、角色卡或邀请链接。</p>
      </section>
    </NCollapseItem>
  </NCollapse>
</template>

<style scoped>
.napcat-guide { margin-bottom: 14px; }
.napcat-guide section { margin: 12px 0; }
.napcat-guide h4 { margin: 0 0 6px; font-size: 14px; color: #d99b45; }
.napcat-guide p { font-size: 13px; line-height: 1.7; margin: 4px 0; color: var(--text, #c9c9c9); }
.napcat-guide ul, .napcat-guide ol { margin: 4px 0 4px 20px; padding: 0; }
.napcat-guide li { font-size: 13px; line-height: 1.7; color: var(--text, #c9c9c9); }
.napcat-guide code { background: rgba(216,173,82,.12); color: #e0b25a; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
.napcat-guide .hint { color: #8a8a8a; font-size: 12px; }
.cmd-table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 12px; }
.cmd-table th, .cmd-table td { border: 1px solid rgba(255,255,255,.12); padding: 5px 7px; text-align: left; color: var(--text, #c9c9c9); }
.cmd-table th { background: rgba(255,255,255,.05); color: #d99b45; }
</style>
