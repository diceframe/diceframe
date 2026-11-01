/** Strings the battlefield needs; the panel owns the copy and passes it in. */
export interface BattlefieldLabels {
  hp: string
  feet: string
  position: string
  condition: (key: string) => string
  selectTarget: (name: string) => string
}
