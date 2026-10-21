import type { Locale } from '@/i18n'

export type ActionRecoveryCode = 'ROUND_PROCESSING_FAILED' | 'ROUND_POST_COMMIT_FAILED' | 'ROUND_NOT_PROCESSED' | 'CHECK_MODEL_NOT_CONFIGURED' | 'ROUND_ROLLBACK_FAILED' | 'UNKNOWN'

export interface ActionRecoveryCopy {
  rolledBack: string
  postCommit: string
  notProcessed: string
  checkModel: string
  rollbackFailed: string
  unknown: string
  refresh: string
  edit: string
  confirmed: string
}

const en: ActionRecoveryCopy = {
  rolledBack: 'Round processing failed and returned to the action phase. Revise your action and try again.',
  postCommit: 'The round may already have advanced. Refresh to see the result; do not submit this action again.',
  notProcessed: 'The round state changed before this action was processed. Refresh to confirm the current result.',
  checkModel: 'The dedicated check model is not configured, so the round cannot advance. Your action is saved; the GM can fix this in Settings and advance again.',
  rollbackFailed: 'The round could not be safely recovered. Refresh the game and verify its current state before trying again.',
  unknown: 'The request result is uncertain. Refresh to check the game before submitting again.',
  refresh: 'Refresh game',
  edit: 'Edit action',
  confirmed: 'Refresh completed. Check the current game state before editing and deciding whether to submit.',
}

const zh: ActionRecoveryCopy = {
  rolledBack: '本轮处理失败，已退回行动阶段。你可以修改行动后重试。',
  postCommit: '本轮可能已经推进，请刷新查看结果；不要重复提交这条行动。',
  notProcessed: '本轮状态在处理前已变化，请刷新确认当前结果。',
  checkModel: '独立检定模型未配置，回合无法推进。行动已保留，请房主在设置中补全配置后再次推进。',
  rollbackFailed: '回合恢复失败，请刷新对局并确认当前状态后再重试。',
  unknown: '请求结果不确定，请刷新查看对局后再决定是否提交。',
  refresh: '刷新对局',
  edit: '编辑行动',
  confirmed: '刷新完成。请先核对对局状态，再编辑并决定是否提交。',
}

const ja: ActionRecoveryCopy = {
  rolledBack: 'ターン処理に失敗し、行動フェーズへ戻りました。行動を修正して再試行できます。',
  postCommit: 'ターンはすでに進んだ可能性があります。再送信せず、再読み込みして結果を確認してください。',
  notProcessed: '処理前にターン状態が変わりました。再読み込みして現在の結果を確認してください。',
  checkModel: '独立判定モデルが未設定のため、ターンを進められません。GM が設定を修正してから再度進行してください。',
  rollbackFailed: 'ターンを安全に復旧できませんでした。再読み込みして状態を確認してから再試行してください。',
  unknown: 'リクエスト結果を確認できません。再読み込みしてから再送信を判断してください。',
  refresh: 'ゲームを再読み込み',
  edit: '行動を編集',
  confirmed: '再読み込みが完了しました。現在の状態を確認してから行動を編集し、送信を判断してください。',
}

const de: ActionRecoveryCopy = {
  rolledBack: 'Die Rundenverarbeitung ist fehlgeschlagen und wurde zur Aktionsphase zurückgesetzt. Überarbeite die Aktion und versuche es erneut.',
  postCommit: 'Die Runde könnte bereits fortgesetzt worden sein. Aktualisiere das Spiel und sende diese Aktion nicht erneut.',
  notProcessed: 'Der Rundenstatus hat sich vor der Verarbeitung geändert. Aktualisiere das Spiel, um das Ergebnis zu prüfen.',
  checkModel: 'Das separate Prüfungsmodell ist nicht konfiguriert. Der GM kann die Einstellung korrigieren und die Runde erneut fortsetzen.',
  rollbackFailed: 'Die Runde konnte nicht sicher wiederhergestellt werden. Aktualisiere das Spiel und prüfe den aktuellen Status.',
  unknown: 'Das Ergebnis der Anfrage ist unklar. Aktualisiere das Spiel, bevor du erneut sendest.',
  refresh: 'Spiel aktualisieren',
  edit: 'Aktion bearbeiten',
  confirmed: 'Aktualisierung abgeschlossen. Prüfe den aktuellen Spielstand, bevor du die Aktion bearbeitest und sendest.',
}

const ru: ActionRecoveryCopy = {
  rolledBack: 'Обработка раунда не удалась, и игра вернулась к фазе действий. Измените действие и повторите попытку.',
  postCommit: 'Раунд мог уже продолжиться. Обновите игру и не отправляйте это действие повторно.',
  notProcessed: 'Состояние раунда изменилось до обработки. Обновите игру, чтобы проверить результат.',
  checkModel: 'Отдельная модель проверок не настроена. GM может исправить настройку и продолжить раунд.',
  rollbackFailed: 'Раунд не удалось безопасно восстановить. Обновите игру и проверьте текущее состояние.',
  unknown: 'Результат запроса неизвестен. Обновите игру перед новой отправкой.',
  refresh: 'Обновить игру',
  edit: 'Изменить действие',
  confirmed: 'Обновление завершено. Проверьте состояние игры, затем измените действие и решите, отправлять ли его.',
}

export const actionRecoveryCopy: Record<Locale, ActionRecoveryCopy> = { en, 'zh-CN': zh, ja, de, ru }

export function actionRecoveryMessage(locale: string, code: ActionRecoveryCode): string {
  const copy = actionRecoveryCopy[locale as Locale] || en
  if (code === 'ROUND_PROCESSING_FAILED') return copy.rolledBack
  if (code === 'ROUND_POST_COMMIT_FAILED') return copy.postCommit
  if (code === 'ROUND_NOT_PROCESSED') return copy.notProcessed
  if (code === 'CHECK_MODEL_NOT_CONFIGURED') return copy.checkModel
  if (code === 'ROUND_ROLLBACK_FAILED') return copy.rollbackFailed
  return copy.unknown
}

export function actionRecoveryConfirmedMessage(locale: string): string {
  return (actionRecoveryCopy[locale as Locale] || en).confirmed
}
