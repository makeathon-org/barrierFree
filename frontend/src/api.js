const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

async function getJSON(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`요청 실패 (${res.status})`)
  return res.json()
}

export function fetchRoute(orig, dest, userType) {
  const q = new URLSearchParams({
    orig_lat: orig.lat,
    orig_lng: orig.lng,
    dest_lat: dest.lat,
    dest_lng: dest.lng,
    user_type: userType,
  })
  return getJSON(`${BASE}/route?${q.toString()}`)
}

export function fetchReports() {
  return getJSON(`${BASE}/reports`)
}

export async function postReport(report) {
  const res = await fetch(`${BASE}/reports`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(report),
  })
  if (!res.ok) throw new Error('제보 등록 실패')
  return res.json()
}

export function fetchWeather() {
  return getJSON(`${BASE}/weather`)
}

export function fetchStairs() {
  return getJSON(`${BASE}/stairs`)
}

export function fetchRecommendation(orig, dest, userType) {
  const q = new URLSearchParams({
    orig_lat: orig.lat,
    orig_lng: orig.lng,
    dest_lat: dest.lat,
    dest_lng: dest.lng,
    user_type: userType,
  })
  return getJSON(`${BASE}/recommend?${q.toString()}`)
}
