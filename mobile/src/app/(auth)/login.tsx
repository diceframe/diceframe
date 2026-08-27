import * as React from 'react'
import { ActivityIndicator, Platform, Pressable, ScrollView, View } from 'react-native'
import { useLocalSearchParams, useRouter } from 'expo-router'

import { PageHeader } from '@/components/page-header'
import { Screen } from '@/components/screen'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Text } from '@/components/ui/text'
import {
  configureApiClient,
  errorMessage,
  fetchAppConfig,
  normalizeBaseUrl,
  validateAccessToken,
} from '@/api/client'
import { useSettingsStore } from '@/stores/settings'
import { strings } from '@/lib/strings'
import { useKeyboardHeight } from '@/lib/use-keyboard-height'

/** 服务器连接 + Owner 登录；已连接时进入即“换服务器”流程 */
export default function LoginScreen() {
  const router = useRouter()
  const { mode } = useLocalSearchParams<{ mode?: string }>()
  const settings = useSettingsStore()

  const [serverUrl, setServerUrl] = React.useState(settings.baseUrl)
  const keyboardHeight = useKeyboardHeight()
  const [passwordNeeded, setPasswordNeeded] = React.useState<boolean | null>(null)
  const [password, setPassword] = React.useState('')
  const [busy, setBusy] = React.useState<'server' | 'login' | null>(null)
  const [error, setError] = React.useState('')

  // Web 端服务器地址可留空 = 使用当前站点（同源相对路径，dev 下由 Metro
  // 的反向代理转发到后端）；原生端必须显式填写局域网地址。
  const isWeb = Platform.OS === 'web'

  // 进入页面时的模式快照：已有服务器 = “换服务器”流程。
  // 用快照而不是响应式读取，避免首次连接成功保存 baseUrl 后页面中途翻转。
  const [switching] = React.useState(() => settings.baseUrl !== '')
  const switchingServer = mode === 'switch'

  // 已连接过服务器时进入本页自动探测：直接显示密码框（或开放服务器直入按钮），
  // 不需要用户先按一次“连接”。
  React.useEffect(() => {
    if ((!settings.baseUrl && !isWeb) || switchingServer) return
    let active = true
    async function probe() {
      setBusy('server')
      try {
        const config = await fetchAppConfig()
        if (active) setPasswordNeeded(!!config.access_password?.configured)
      } catch {
        // 探测失败（服务器离线等）：留在手动流程，由用户重按连接
      } finally {
        if (active) setBusy(null)
      }
    }
    probe()
    return () => {
      active = false
    }
  }, [settings.baseUrl, switchingServer, isWeb])

  async function connectServer() {
    const normalized = normalizeBaseUrl(serverUrl)
    if (!normalized && !isWeb) {
      setError(strings.common.networkError)
      return
    }
    setBusy('server')
    setError('')
    try {
      configureApiClient({ baseUrl: normalized })
      const config = await fetchAppConfig()
      if (normalized !== settings.baseUrl) {
        // 新服务器：本机的 GM 密码与玩家身份一律作废
        settings.setToken(null)
        settings.setShare(null)
      }
      settings.setBaseUrl(normalized)
      setPasswordNeeded(!!config.access_password?.configured)
    } catch (e) {
      const detail = errorMessage(e)
      const target = normalized || '当前地址'
      setError(detail ? `${strings.common.networkError}（${target}：${detail}）` : strings.common.networkError)
    } finally {
      setBusy(null)
    }
  }

  async function login() {
    setBusy('login')
    setError('')
    try {
      await validateAccessToken(password)
      settings.setToken(password)
      settings.setShare(null)
      router.replace('/overview')
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : strings.login.wrongPassword)
    } finally {
      setBusy(null)
    }
  }

  function enterOpen() {
    settings.setShare(null)
    router.replace('/overview')
  }

  return (
    <Screen style={{ width: '100%', maxWidth: 600, alignSelf: 'center' }}>
      {switching ? (
        <PageHeader title="切换服务器" onBack={() => router.back()} />
      ) : (
        <View className="h-3" />
      )}

      {/* 键盘避让：底部垫高键盘实际高度，表单区可滚动（见 use-keyboard-height 注释） */}
      <View className="flex-1" style={{ paddingBottom: keyboardHeight }}>
        <ScrollView
          className="flex-1"
          contentContainerClassName="flex-grow justify-center gap-6 px-6"
          keyboardShouldPersistTaps="handled"
        >
        <View className="items-center gap-2">
          <Text variant="h1">DiceFrame</Text>
          <Text variant="muted">{strings.login.title}</Text>
        </View>

        <View className="gap-3">
          <Text variant="small">{strings.login.serverLabel}</Text>
          <Input
            value={serverUrl}
            onChangeText={setServerUrl}
            placeholder={isWeb ? strings.login.serverPlaceholderWeb : strings.login.serverPlaceholder}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            editable={busy === null}
          />
          <Button onPress={connectServer} disabled={busy !== null}>
            {busy === 'server' ? (
              <ActivityIndicator className="text-primary-foreground" />
            ) : (
              <Text>{switching ? '切换服务器' : strings.login.connect}</Text>
            )}
          </Button>
        </View>

        {passwordNeeded !== null && (
          <View className="gap-3">
            {passwordNeeded ? (
              <>
                <Text variant="small">{strings.login.passwordLabel}</Text>
                <Input
                  value={password}
                  onChangeText={setPassword}
                  placeholder={strings.login.passwordPlaceholder}
                  secureTextEntry
                  editable={busy === null}
                />
                <Button onPress={login} disabled={busy !== null || !password}>
                  {busy === 'login' ? (
                    <ActivityIndicator className="text-primary-foreground" />
                  ) : (
                    <Text>{strings.login.login}</Text>
                  )}
                </Button>
              </>
            ) : (
              <Button variant="secondary" onPress={enterOpen} disabled={busy !== null}>
                <Text>{strings.login.enterOpen}</Text>
              </Button>
            )}
          </View>
        )}

        {error ? <Text className="text-destructive">{error}</Text> : null}

        <Pressable
          className="items-center py-2"
          onPress={() => router.push('/join')}
          accessibilityRole="link"
        >
          <Text className="text-primary">{strings.login.joinInstead}</Text>
        </Pressable>
        </ScrollView>
      </View>
    </Screen>
  )
}
