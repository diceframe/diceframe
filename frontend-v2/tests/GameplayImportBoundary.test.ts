// @vitest-environment node
import { ESLint } from 'eslint'
import { describe, expect, it } from 'vitest'

// ADR-0013: features/play and features/gal stay apart. The specifiers are
// matched as written, so every spelling of the same import must be caught.
const eslint = new ESLint({ cwd: process.cwd() })

async function boundaryErrors(filePath: string, source: string): Promise<number> {
  const [result] = await eslint.lintText(`${source}\nexport default x\n`, { filePath })
  return result!.messages.filter(message => (
    message.ruleId === 'no-restricted-imports' || message.ruleId === 'no-restricted-syntax'
  )).length
}

describe('gameplay feature import boundary', () => {
  it.each([
    "import x from '@/features/gal/GalPlayView.vue'",
    "import x from '../../gal/a'",
    "import x from '../../features/gal/a'",
    "const x = () => import('@/features/gal/a')",
  ])('keeps features/play out of gal: %s', async (source) => {
    expect(await boundaryErrors('src/features/play/sub/X.ts', source)).toBe(1)
  })

  it.each([
    "import x from '@/features/play/stores/a'",
    "import x from '../../play/a'",
    "import x from '../../features/play/a'",
    "const x = () => import('@/features/play/a')",
    "import x from '../play'",
  ])('lets gal reach features/play only through PlayView: %s', async (source) => {
    expect(await boundaryErrors('src/features/gal/sub/X.ts', source)).toBe(1)
  })

  it.each([
    ['src/features/gal/X.ts', "import x from '@/features/play/PlayView.vue'"],
    ['src/features/gal/X.ts', "import x from '../play/PlayView.vue'"],
    ['src/features/gal/X.ts', "import x from '@/components/play/Foo.vue'"],
    ['src/features/play/sub/X.ts', "import x from '../../components/gallery/a'"],
  ])('allows %s to import: %s', async (filePath, source) => {
    expect(await boundaryErrors(filePath, source)).toBe(0)
  })
})
