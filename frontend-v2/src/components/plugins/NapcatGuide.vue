<script setup lang="ts">
import { NCollapse, NCollapseItem } from 'naive-ui'
import { useLocale } from '@/composables/useLocale'
import type { MessageKey } from '@/i18n'

const { t } = useLocale()

const commandRows: { command: string; desc: MessageKey; role: MessageKey }[] = [
  { command: '@bot \u5e2e\u52a9', desc: 'napcatCmdHelpDesc', role: 'napcatRoleEveryone' },
  { command: '@bot \u524d\u60c5', desc: 'napcatCmdRecapDesc', role: 'napcatRoleEveryone' },
  { command: '@bot \u5730\u56fe', desc: 'napcatCmdMapDesc', role: 'napcatRoleEveryone' },
  { command: '@bot \u9080\u8bf7', desc: 'napcatCmdInviteDesc', role: 'napcatRoleEveryone' },
  { command: '@bot \u65b0\u5efa\u89d2\u8272 / \u8f66\u5361', desc: 'napcatCmdNewCharacterDesc', role: 'napcatRoleEveryone' },
  { command: '@bot \u52a0\u5165 \u89d2\u8272\u540d', desc: 'napcatCmdJoinDesc', role: 'napcatRolePlayer' },
  { command: '@bot \u7ed1\u5b9a <key> <token>', desc: 'napcatCmdBindDesc', role: 'napcatRoleGm' },
  { command: '@bot \u72b6\u6001', desc: 'napcatCmdStatusDesc', role: 'napcatRoleClaimed' },
  { command: '@bot \u611f\u77e5', desc: 'napcatCmdPerceptionDesc', role: 'napcatRoleClaimed' },
  { command: '@bot \u652f\u4ed8', desc: 'napcatCmdPaymentDesc', role: 'napcatRoleClaimed' },
  { command: '@bot \u786e\u8ba4\u652f\u4ed8 / \u62d2\u7edd\u652f\u4ed8', desc: 'napcatCmdPaymentConfirmDesc', role: 'napcatRoleClaimed' },
  { command: '@bot \u63b7\u9ab0', desc: 'napcatCmdRollDesc', role: 'napcatRoleClaimed' },
  { command: '@bot \u63a8\u8fdb / \u4e0b\u4e00\u8f6e', desc: 'napcatCmdAdvanceDesc', role: 'napcatRoleGmOrAllowed' },
  { command: '@bot \u6682\u79bb / \u56de\u6765', desc: 'napcatCmdAwayDesc', role: 'napcatRoleClaimedOrGm' },
  { command: '@bot <action>', desc: 'napcatCmdActionDesc', role: 'napcatRoleClaimed' },
]
</script>

<template>
  <NCollapse :default-expanded-names="['guide']" class="napcat-guide" arrow-placement="right">
    <NCollapseItem :title="t('napcatGuideTitle')" name="guide">
      <section>
        <h4>{{ t('napcatWhatTitle') }}</h4>
        <p>{{ t('napcatWhatBody') }} <code>@bot</code></p>
      </section>

      <section>
        <h4>{{ t('napcatInstallTitle') }}</h4>
        <p>{{ t('napcatInstallBody') }} <strong>{{ t('napcatNetworkPath') }}</strong>{{ t('napcatInstallBodySuffix') }}</p>
        <ul>
          <li><strong>{{ t('port') }}</strong>{{ t('napcatDefaultPort') }}</li>
          <li><strong>access_token</strong>{{ t('napcatAccessTokenHint') }}</li>
        </ul>
      </section>

      <section>
        <h4>{{ t('napcatFillTitle') }}</h4>
        <p>{{ t('napcatFillBody') }}</p>
        <ul>
          <li><strong>{{ t('hostAddress') }}</strong> = {{ t('napcatHostHelp') }} <code>127.0.0.1</code></li>
          <li><strong>{{ t('port') }}</strong> = {{ t('napcatPortHelp') }}</li>
          <li><strong>{{ t('accessToken') }}</strong> = {{ t('napcatTokenHelp') }}</li>
        </ul>
        <p>{{ t('napcatSaveRestartPrefix') }} <strong>{{ t('saveConfig') }}</strong>{{ t('napcatSaveRestartMiddle') }} <strong>{{ t('restartPlugin') }}</strong>{{ t('napcatSaveRestartSuffix') }} <code>running</code>{{ t('napcatRunningSuffix') }}</p>
      </section>

      <section>
        <h4>{{ t('napcatFilterTitle') }}</h4>
        <p><strong>{{ t('enableChatAllowlist') }}</strong>:</p>
        <ul>
          <li>{{ t('napcatFilterOff') }} <code>@bot</code></li>
          <li>{{ t('napcatFilterOn') }}</li>
        </ul>
        <p class="hint">{{ t('napcatFilterHint') }}</p>
      </section>

      <section>
        <h4>{{ t('napcatHostStartTitle') }}</h4>
        <ol>
          <li>{{ t('napcatHostStepCreate') }}</li>
          <li>{{ t('napcatHostStepBindPrefix') }} <strong>{{ t('gmControls') }}</strong>{{ t('napcatHostStepBindMiddle') }} <strong>{{ t('oneTimeBotBind') }}</strong>{{ t('napcatHostStepBindSuffix') }} <code>{{ '\u7ed1\u5b9a <game_key> <token>' }}</code></li>
          <li>{{ t('napcatHostStepSend') }} <strong>@bot</strong></li>
          <li>{{ t('napcatHostStepDone') }}</li>
        </ol>
        <p class="hint">{{ t('napcatBindSecurityHint') }}</p>
      </section>

      <section>
        <h4>{{ t('napcatInviteTitle') }}</h4>
        <p>{{ t('napcatInviteBody') }}</p>
        <table class="cmd-table">
          <thead><tr><th>{{ t('command') }}</th><th>{{ t('purpose') }}</th><th>{{ t('whoCanUse') }}</th></tr></thead>
          <tbody>
            <tr v-for="row in commandRows" :key="row.command">
              <td><code>{{ row.command }}</code></td>
              <td>{{ t(row.desc) }}</td>
              <td>{{ t(row.role) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="hint">{{ t('napcatRoundHint') }}</p>
      </section>

      <section>
        <h4>{{ t('napcatCardCacheTitle') }}</h4>
        <p>{{ t('napcatCardCacheBodyPrefix') }} <code>data/bot/cards</code>{{ t('napcatCardCacheBodySuffix') }} <strong>{{ t('clearCardCache') }}</strong>{{ t('napcatCardCacheBodyEnd') }}</p>
        <p class="hint">{{ t('napcatCardCacheHintPrefix') }} <code>card_*.png</code>{{ t('napcatCardCacheHintSuffix') }}</p>
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
