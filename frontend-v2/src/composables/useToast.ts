import { getMessage } from './useNaiveBridge'

export function useToast() {
  const api = getMessage()
  return {
    success: (content: string) => api.success(content),
    error: (content: string) => api.error(content, { duration: 6000 }),
    info: (content: string) => api.info(content),
    warning: (content: string) => api.warning(content),
  }
}
