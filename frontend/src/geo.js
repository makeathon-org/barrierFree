// 지리 계산 유틸

export function haversine(lat1, lng1, lat2, lng2) {
  const R = 6371000
  const toRad = (d) => (d * Math.PI) / 180
  const dLat = toRad(lat2 - lat1)
  const dLng = toRad(lng2 - lng1)
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(a))
}

/**
 * 경로 coords([[lat,lng],...]) 를 약 stepM 간격으로 잘게 보간한 점 목록으로.
 * 안내 시뮬레이션 마커가 부드럽게 이동하고, 제보 근접 판정이 촘촘해진다.
 */
export function densify(coords, stepM = 12) {
  if (!coords || coords.length < 2) return coords ? [...coords] : []
  const out = []
  for (let i = 0; i < coords.length - 1; i++) {
    const [lat1, lng1] = coords[i]
    const [lat2, lng2] = coords[i + 1]
    const segLen = haversine(lat1, lng1, lat2, lng2)
    const n = Math.max(1, Math.ceil(segLen / stepM))
    for (let k = 0; k < n; k++) {
      const t = k / n
      out.push([lat1 + (lat2 - lat1) * t, lng1 + (lng2 - lng1) * t])
    }
  }
  out.push(coords[coords.length - 1])
  return out
}
