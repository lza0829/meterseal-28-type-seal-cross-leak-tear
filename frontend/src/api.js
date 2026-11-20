const base = '/api'

async function req(path, opts = {}) {
  const res = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) {
    const msg = (data && data.detail) || text || res.statusText
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data
}

export const api = {
  overview: () => req('/overview'),
  meters: () => req('/meters'),
  estimates: (periodId, meterType) => {
    const sp = new URLSearchParams()
    if (periodId) sp.set('period_id', periodId)
    if (meterType) sp.set('meter_type', meterType)
    const s = sp.toString()
    return req('/estimates' + (s ? `?${s}` : ''))
  },
  actuals: (periodId, meterType) => {
    const sp = new URLSearchParams()
    if (periodId) sp.set('period_id', periodId)
    if (meterType) sp.set('meter_type', meterType)
    const s = sp.toString()
    return req('/actuals' + (s ? `?${s}` : ''))
  },
  postEstimate: (body) => req('/estimates', { method: 'POST', body: JSON.stringify(body) }),
  postActual: (body) => req('/actuals', { method: 'POST', body: JSON.stringify(body) }),
  periods: () => req('/periods'),
  // 对齐与封印都必须带 meter_type
  align: (period_id, meter_type) =>
    req('/align', { method: 'POST', body: JSON.stringify({ period_id, meter_type }) }),
  seal: (body) => req('/seal', { method: 'POST', body: JSON.stringify(body) }),
  batches: (periodId, meterType) => {
    const sp = new URLSearchParams()
    if (periodId) sp.set('period_id', periodId)
    if (meterType) sp.set('meter_type', meterType)
    const s = sp.toString()
    return req('/batches' + (s ? `?${s}` : ''))
  },
  variances: (q) => {
    const sp = new URLSearchParams(q || {})
    const s = sp.toString()
    return req('/variances' + (s ? `?${s}` : ''))
  },
  setFate: (id, fate) => req(`/variances/${id}/fate`, { method: 'POST', body: JSON.stringify({ fate }) }),
  settings: () => req('/settings'),
  putSetting: (key, value) => req(`/settings/${key}`, { method: 'PUT', body: JSON.stringify({ value }) }),
}
