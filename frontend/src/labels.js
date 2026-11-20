/** 界面文案映射：库内码 → 中文（页面不直接露英文码） */
export const 表类型 = { water: '水表', electric: '电表', 水表: '水表', 电表: '电表' }
export const 表状态 = { active: '在用', inactive: '停用', 在用: '在用', 停用: '停用' }
export const 写闸状态 = { open: '开放', sealed: '已封印' }
export const 账期状态 = { open: '开放', sealed: '已封印', expired: '已作废', 开放: '开放', 已封印: '已封印' }
export const 批状态 = { open: '开放', sealed: '已封印', expired: '已作废' }
export const 命运 = {
  pending: '未决',
  accept_short: '认短',
  accept_over: '认溢',
  quarantine: '隔离',
}

export function 标(map, key) {
  if (key == null || key === '') return '—'
  return map[key] || String(key)
}
