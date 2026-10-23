import vue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

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
  // Gameplay packages stay apart (ADR-0012): the tabletop play feature never
  // reaches into another mode's feature, and gal only reuses the tabletop
  // PlayView entry while its v0 preview delegates to it.
  {
    files: ['src/features/play/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': ['error', { patterns: [{ group: ['@/features/gal/**', '**/features/gal/**', '../gal/**'], message: 'features/play must not depend on the gal gameplay feature.' }] }],
    },
  },
  {
    files: ['src/features/gal/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': ['error', { patterns: [{ group: ['@/features/play/**', '!@/features/play/PlayView.vue', '../play/**'], message: 'features/gal may only reuse the PlayView entry from features/play; move shared pieces to components/ or composables/.' }] }],
    },
  },
]
