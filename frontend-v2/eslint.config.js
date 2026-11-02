import vue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

// Import specifiers that reach another gameplay feature: the `@/features/...`
// alias, or a relative path of any depth (`../gal`, `../../gal`,
// `../../features/gal`). gal may still reuse the tabletop PlayView entry.
// `\x2F` is "/": the same pattern is embedded in an esquery selector, whose
// regex literal cannot contain a slash.
const PLAY_TO_GAL = String.raw`^(@\x2Ffeatures\x2F|(\.\.\x2F)+(features\x2F)?)gal(\x2F|$)`
const GAL_TO_PLAY = String.raw`^(@\x2Ffeatures\x2F|(\.\.\x2F)+(features\x2F)?)play(\x2F(?!PlayView\.vue$)|$)`
const PLAY_TO_GAL_MESSAGE = 'features/play must not depend on the gal gameplay feature.'
const GAL_TO_PLAY_MESSAGE = 'features/gal may only reuse the PlayView entry from features/play; move shared pieces to components/ or composables/.'

export default [
  {
    ignores: ['dist/**', 'node_modules/**'],
  },
  ...tseslint.configs.recommended,
  ...vue.configs['flat/essential'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: ['.vue'],
      },
    },
  },
  {
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'vue/multi-word-component-names': 'off',
    },
  },
  // Gameplay packages stay apart (ADR-0013): the tabletop play feature never
  // reaches into another mode's feature, and gal only reuses the tabletop
  // PlayView entry while its v0 preview delegates to it.
  // Specifiers are matched as written, so each rule covers the alias, any
  // depth of relative path, and dynamic import() (no-restricted-imports skips
  // those).
  {
    files: ['src/features/play/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': ['error', { patterns: [{ regex: PLAY_TO_GAL, message: PLAY_TO_GAL_MESSAGE }] }],
      'no-restricted-syntax': ['error', { selector: `ImportExpression[source.value=/${PLAY_TO_GAL}/]`, message: PLAY_TO_GAL_MESSAGE }],
    },
  },
  {
    files: ['src/features/gal/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': ['error', { patterns: [{ regex: GAL_TO_PLAY, message: GAL_TO_PLAY_MESSAGE }] }],
      'no-restricted-syntax': ['error', { selector: `ImportExpression[source.value=/${GAL_TO_PLAY}/]`, message: GAL_TO_PLAY_MESSAGE }],
    },
  },
]
