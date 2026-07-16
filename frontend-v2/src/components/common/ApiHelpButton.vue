<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NIcon, NModal } from 'naive-ui'
import { HelpCircleOutline } from '@vicons/ionicons5'

const show = ref(false)
</script>

<template>
  <NButton text class="api-help-btn" @click="show = true">
    <template #icon>
      <NIcon :component="HelpCircleOutline" />
    </template>
    帮助
  </NButton>
  <NModal v-model:show="show" preset="card" title="如何获取 DeepSeek API Key" style="max-width:520px" :bordered="false">
    <div class="api-tutorial">
      <h4>1. 注册 DeepSeek 账号</h4>
      <p>打开 <a href="https://platform.deepseek.com/" target="_blank" rel="noopener">DeepSeek 开放平台</a>，注册并登录。</p>
      <h4>2. 创建 API Key</h4>
      <p>进入左侧「API Keys」页面，点击「创建 API Key」，给个名字后复制生成的 Key（形如 <code>sk-xxxxxxxx</code>）。</p>
      <h4>3. 填写到 DiceFrame</h4>
      <p>回到本页的「主模型接口」，按以下内容填写：</p>
      <ul>
        <li><strong>接口格式</strong>：OpenAI 兼容</li>
        <li><strong>Base URL</strong>：<code>https://api.deepseek.com/v1</code></li>
        <li><strong>API Key</strong>：粘贴刚才复制的 Key</li>
        <li><strong>模型</strong>：<code>deepseek-v4-pro</code></li>
      </ul>
      <p>填好后点「保存」，再点「测试连接」确认能通。</p>
    </div>
  </NModal>
</template>

<style scoped>
.api-help-btn{font-size:13px;font-weight:400}
.api-tutorial h4{margin:14px 0 4px}
.api-tutorial h4:first-child{margin-top:0}
.api-tutorial p{margin:4px 0;line-height:1.6}
.api-tutorial ul{margin:6px 0;padding-left:20px;line-height:1.8}
.api-tutorial code{background:rgba(216,173,82,.12);padding:1px 5px;border-radius:4px;font-family:var(--font-mono);font-size:12px}
</style>
