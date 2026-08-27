const { withAndroidManifest } = require('@expo/config-plugins')

/**
 * The client connects to user-provided DiceFrame HTTP endpoints, including
 * LAN addresses. Expo does not map android.usesCleartextTraffic from app.json
 * into release manifests, so set the application attribute explicitly.
 */
module.exports = function withCleartextTraffic(config) {
  return withAndroidManifest(config, (configWithManifest) => {
    const application = configWithManifest.modResults.manifest.application?.[0]
    if (application) {
      application.$ = application.$ || {}
      application.$['android:usesCleartextTraffic'] = 'true'
    }
    return configWithManifest
  })
}
