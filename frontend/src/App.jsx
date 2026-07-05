import { useEffect, useRef, useState, useCallback } from 'react'
import { useKakao } from './useKakao'
import {
  fetchRoute,
  fetchReports,
  postReport,
  fetchWeather,
  fetchStairs,
  fetchRecommendation,
} from './api'
import { haversine, densify } from './geo'

const CENTER = { lat: 37.5925, lng: 127.0163 }

const USER_TYPES = [
  { key: 'wheelchair', label: '휠체어', icon: '♿' },
  { key: 'elderly', label: '노약자', icon: '🧓' },
  { key: 'leg_injury', label: '다리 부상', icon: '🩼' },
  { key: 'heavy_luggage', label: '무거운 짐', icon: '🧳' },
]

// 위험 제보 유형
const REPORT_TYPES = [
  { key: '빙판길', icon: '🧊' },
  { key: '공사중', icon: '🚧' },
  { key: '시설 고장', icon: '🛠️' },
  { key: '정보 오류', icon: '❗' },
]

const STRATEGY_META = {
  safe: {
    label: '안전',
    title: '안전 최우선',
    icon: '🛡️',
    color: '#1D9E75',
    desc: '계단을 피하고 완만한 길로 안내해요',
  },
  balanced: {
    label: '균형',
    title: '균형 경로',
    icon: '🧭',
    color: '#EF9F27',
    desc: '시간과 안전을 균형있게 고려했어요',
  },
  fast: {
    label: '빠름',
    title: '시간 최우선',
    icon: '⚡',
    color: '#378ADD',
    desc: '가장 빠르지만 위험 구간이 있어요',
  },
}
const STRATEGY_ORDER = ['safe', 'balanced', 'fast']

// 제보 근접 경고 임계 (m)
const ALERT_RADIUS = 30

// 경로 구간 경사 3단계 (segment_grades: 0~1 비율)
const ROUTE_SLOPE_LEVELS = [
  { max: 0.03, color: '#1D9E75', label: '낮음 (~3%)' },
  { max: 0.08, color: '#EF9F27', label: '중간 (3~8%)' },
  { max: Infinity, color: '#E24B4A', label: '높음 (8%~)' },
]
function routeSlopeColor(grade) {
  return ROUTE_SLOPE_LEVELS.find((l) => grade < l.max).color
}

/** coords(N개) 와 grades(N-1개) 를 같은 경사단계끼리 묶어 구간 polyline 들로 분할. */
function splitByGrade(coords, grades) {
  if (!coords || coords.length < 2 || !grades || !grades.length) {
    return coords && coords.length >= 2
      ? [{ color: ROUTE_SLOPE_LEVELS[0].color, grade: 0, path: coords }]
      : []
  }
  const runs = []
  let start = 0
  let curColor = routeSlopeColor(grades[0])
  let curMax = grades[0]
  for (let s = 1; s < grades.length; s++) {
    const c = routeSlopeColor(grades[s])
    if (c !== curColor) {
      runs.push({ color: curColor, grade: curMax, path: coords.slice(start, s + 1) })
      start = s
      curColor = c
      curMax = grades[s]
    } else {
      curMax = Math.max(curMax, grades[s])
    }
  }
  runs.push({ color: curColor, grade: curMax, path: coords.slice(start, grades.length + 1) })
  return runs
}

export default function App() {
  const { ready, error: kakaoError } = useKakao()

  const mapEl = useRef(null)
  const mapRef = useRef(null)
  const origMarkerRef = useRef(null)
  const destMarkerRef = useRef(null)
  const reportMarkersRef = useRef([])
  const polylinesRef = useRef({})
  const navMarkerRef = useRef(null)
  const navTimerRef = useRef(null)
  const placesRef = useRef(null)
  const modeRef = useRef('dest')
  const gradePopupRef = useRef(null)
  const stairsLayerRef = useRef([])
  const stairsPopupRef = useRef(null)
  const reportMapEl = useRef(null)
  const reportMapRef = useRef(null)

  const [userType, setUserType] = useState('wheelchair')
  const [panelStep, setPanelStep] = useState('input') // 'input' | 'results'
  const [sheetExpanded, setSheetExpanded] = useState(false) // 모바일 바텀시트 펼침
  const [mode, setMode] = useState('dest') // 'orig' | 'dest' | 'report'
  // 기본값: 우회가 잘 드러나는 데모용 출발/도착 (지도 클릭·검색으로 변경 가능)
  const [orig, setOrig] = useState({ lat: 37.5894, lng: 127.0197 })
  const [dest, setDest] = useState({ lat: 37.5916, lng: 127.018 })
  const [origLabel, setOrigLabel] = useState('지도에서 선택한 지점')
  const [destLabel, setDestLabel] = useState('지도에서 선택한 지점')

  const [routes, setRoutes] = useState(null)
  const [selected, setSelected] = useState('safe')
  const [loading, setLoading] = useState(false)
  const [apiError, setApiError] = useState(null)
  const [recommendation, setRecommendation] = useState(null)
  const [recLoading, setRecLoading] = useState(false)

  const [reports, setReports] = useState([])
  const [weather, setWeather] = useState(null)
  const [toast, setToast] = useState(null)
  const [navRunning, setNavRunning] = useState(false)
  const [weatherPopup, setWeatherPopup] = useState(false)
  const [reportOpen, setReportOpen] = useState(false)
  const [reportType, setReportType] = useState(REPORT_TYPES[0].key)
  const [reportDesc, setReportDesc] = useState('')
  const [reportLoc, setReportLoc] = useState(null)
  const [stairs, setStairs] = useState([])
  const [showStairs, setShowStairs] = useState(true)
  const [showReports, setShowReports] = useState(true)

  const [keyword, setKeyword] = useState('')
  const [searchResults, setSearchResults] = useState([])

  // modeRef 동기화 (지도 클릭 리스너에서 최신 mode 참조용)
  useEffect(() => {
    modeRef.current = mode
  }, [mode])

  const showToast = useCallback((msg, ms = 4000) => {
    setToast(msg)
    window.clearTimeout(showToast._t)
    showToast._t = window.setTimeout(() => setToast(null), ms)
  }, [])

  // ── 지도 초기화 ──────────────────────────────────────────────
  useEffect(() => {
    if (!ready || mapRef.current) return
    const { kakao } = window
    const map = new kakao.maps.Map(mapEl.current, {
      center: new kakao.maps.LatLng(CENTER.lat, CENTER.lng),
      level: 4,
    })
    mapRef.current = map
    placesRef.current = new kakao.maps.services.Places()

    kakao.maps.event.addListener(map, 'click', (e) => {
      const lat = e.latLng.getLat()
      const lng = e.latLng.getLng()
      const m = modeRef.current
      if (m === 'orig') {
        setOrig({ lat, lng })
        setOrigLabel('지도에서 선택한 지점')
      } else if (m === 'dest') {
        setDest({ lat, lng })
        setDestLabel('지도에서 선택한 지점')
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready])

  // ── 초기 데이터 ──────────────────────────────────────────────
  useEffect(() => {
    fetchReports().then(setReports).catch(() => {})
    fetchWeather().then(setWeather).catch(() => {})
    fetchStairs().then((d) => setStairs(d.stairs || [])).catch(() => {})
  }, [])

  // ── 출발/도착 마커 ───────────────────────────────────────────
  useEffect(() => {
    if (!mapRef.current) return
    const { kakao } = window
    origMarkerRef.current?.setMap(null)
    if (orig) {
      const mk = new kakao.maps.Marker({
        position: new kakao.maps.LatLng(orig.lat, orig.lng),
        map: mapRef.current,
        image: pinImage('#1D9E75', '출'),
      })
      origMarkerRef.current = mk
    }
  }, [orig, ready])

  useEffect(() => {
    if (!mapRef.current) return
    const { kakao } = window
    destMarkerRef.current?.setMap(null)
    if (dest) {
      const mk = new kakao.maps.Marker({
        position: new kakao.maps.LatLng(dest.lat, dest.lng),
        map: mapRef.current,
        image: pinImage('#D64545', '도'),
      })
      destMarkerRef.current = mk
    }
  }, [dest, ready])

  // ── 제보 마커 ────────────────────────────────────────────────
  useEffect(() => {
    if (!mapRef.current) return
    const { kakao } = window
    reportMarkersRef.current.forEach((m) => m.setMap(null))
    reportMarkersRef.current = []
    if (!showReports) return
    reportMarkersRef.current = reports.map((r) => {
      const mk = new kakao.maps.Marker({
        position: new kakao.maps.LatLng(r.lat, r.lng),
        map: mapRef.current,
        image: pinImage('#9333EA', '!'),
      })
      const iw = new kakao.maps.InfoWindow({
        content: `<div style="padding:6px 10px;font-size:12px;white-space:nowrap;">
          <b>⚠ ${escapeHtml(r.type)}</b><br/>${escapeHtml(r.desc || '')}</div>`,
      })
      kakao.maps.event.addListener(mk, 'click', () => iw.open(mapRef.current, mk))
      return mk
    })
  }, [reports, ready, showReports])

  // ── 계단 레이어 ──────────────────────────────────────────────
  useEffect(() => {
    if (!mapRef.current) return
    const { kakao } = window
    stairsLayerRef.current.forEach((o) => o.setMap(null))
    stairsLayerRef.current = []
    if (!showStairs) return
    if (!stairsPopupRef.current) {
      stairsPopupRef.current = new kakao.maps.CustomOverlay({ yAnchor: 1.5, zIndex: 21 })
    }
    const popup = stairsPopupRef.current
    const objs = []
    stairs.forEach((s) => {
      const path = s.coords.map(([lat, lng]) => new kakao.maps.LatLng(lat, lng))
      const midLat = (s.coords[0][0] + s.coords[1][0]) / 2
      const midLng = (s.coords[0][1] + s.coords[1][1]) / 2
      const mid = new kakao.maps.LatLng(midLat, midLng)
      const line = new kakao.maps.Polyline({
        path,
        strokeWeight: 6,
        strokeColor: '#E24B4A',
        strokeOpacity: 0.95,
        strokeStyle: 'solid',
        zIndex: 4,
      })
      line.setMap(mapRef.current)
      const marker = new kakao.maps.Marker({
        position: mid,
        map: mapRef.current,
        image: stairsIcon(),
        zIndex: 4,
        title: '계단',
      })
      const showPop = (e) => {
        popup.setContent('<div class="stairs-popup">⚠️ 계단 구간</div>')
        popup.setPosition(e && e.latLng ? e.latLng : mid)
        popup.setMap(mapRef.current)
      }
      const hidePop = () => popup.setMap(null)
      kakao.maps.event.addListener(line, 'mouseover', showPop)
      kakao.maps.event.addListener(line, 'mousemove', (e) => {
        if (e && e.latLng) popup.setPosition(e.latLng)
      })
      kakao.maps.event.addListener(line, 'mouseout', hidePop)
      kakao.maps.event.addListener(marker, 'mouseover', () => showPop())
      kakao.maps.event.addListener(marker, 'mouseout', hidePop)
      objs.push(line, marker)
    })
    stairsLayerRef.current = objs
  }, [stairs, showStairs, ready])

  // ── 경로 폴리라인 ────────────────────────────────────────────
  // 비선택 경로: 전략 색(초록/주황/파랑) 얇게. 선택 경로: 경사 단계별 색으로 분할.
  useEffect(() => {
    if (!mapRef.current) return
    const { kakao } = window
    Object.values(polylinesRef.current)
      .flat()
      .forEach((p) => p.setMap(null))
    polylinesRef.current = {}
    gradePopupRef.current?.setMap(null)
    if (!routes) return

    if (!gradePopupRef.current) {
      gradePopupRef.current = new kakao.maps.CustomOverlay({ yAnchor: 1.4, zIndex: 20 })
    }
    const popup = gradePopupRef.current

    const bounds = new kakao.maps.LatLngBounds()
    // 비선택 경로 먼저(아래), 선택 경로 나중(위)
    const order = [...STRATEGY_ORDER].sort((a, b) =>
      a === selected ? 1 : b === selected ? -1 : 0,
    )
    order.forEach((key) => {
      const r = routes[key]
      if (!r || !r.coords || r.coords.length < 2) return
      const isSel = key === selected

      if (!isSel) {
        const path = r.coords.map(([lat, lng]) => new kakao.maps.LatLng(lat, lng))
        const line = new kakao.maps.Polyline({
          path,
          strokeWeight: 5,
          strokeColor: STRATEGY_META[key].color,
          strokeOpacity: 0.4,
          strokeStyle: 'solid',
          zIndex: 5,
        })
        line.setMap(mapRef.current)
        kakao.maps.event.addListener(line, 'click', () => setSelected(key))
        polylinesRef.current[key] = [line]
        return
      }

      // 선택 경로 → 경사 단계별 분할
      const runs = splitByGrade(r.coords, r.segment_grades)
      polylinesRef.current[key] = runs.map((run) => {
        const path = run.path.map(([lat, lng]) => new kakao.maps.LatLng(lat, lng))
        path.forEach((pt) => bounds.extend(pt))
        const line = new kakao.maps.Polyline({
          path,
          strokeWeight: 8,
          strokeColor: run.color,
          strokeOpacity: 0.95,
          strokeStyle: 'solid',
          zIndex: 7,
        })
        line.setMap(mapRef.current)
        // hover 시 경사 % 팝업
        kakao.maps.event.addListener(line, 'mouseover', (e) => {
          popup.setContent(
            `<div class="grade-popup">경사 ${(run.grade * 100).toFixed(1)}%</div>`,
          )
          popup.setPosition(e ? e.latLng : path[Math.floor(path.length / 2)])
          popup.setMap(mapRef.current)
        })
        kakao.maps.event.addListener(line, 'mousemove', (e) => {
          if (e) popup.setPosition(e.latLng)
        })
        kakao.maps.event.addListener(line, 'mouseout', () => popup.setMap(null))
        return line
      })
    })
    if (!bounds.isEmpty()) mapRef.current.setBounds(bounds)
  }, [routes, selected, ready])

  // ── 경로 찾기 ────────────────────────────────────────────────
  async function handleFindRoute() {
    if (!orig || !dest) {
      showToast('출발지와 도착지를 먼저 지정하세요.')
      return
    }
    stopNavigation()
    setLoading(true)
    setApiError(null)
    setRecommendation(null)
    try {
      const data = await fetchRoute(orig, dest, userType)
      setRoutes(data.routes)
      // 유효한 경로 중 첫 번째를 선택
      const firstValid =
        STRATEGY_ORDER.find((k) => data.routes[k]?.coords?.length >= 2) || 'safe'
      setSelected(firstValid)
      setPanelStep('results') // 결과 화면으로 전환
      setSheetExpanded(true) // 모바일에서 결과 시트 펼치기

      // AI 추천은 비동기로(지도는 즉시 그려지고, 추천은 도착하는 대로 표시)
      setRecLoading(true)
      fetchRecommendation(orig, dest, userType)
        .then((res) => {
          setRecommendation(res.recommendation)
          const rec = res.recommendation?.recommended
          if (rec && data.routes[rec]?.coords?.length >= 2) setSelected(rec)
        })
        .catch(() => {})
        .finally(() => setRecLoading(false))
    } catch (e) {
      setApiError(e.message || '경로를 불러오지 못했습니다.')
      setRoutes(null)
    } finally {
      setLoading(false)
    }
  }

  // ── 제보 추가 ────────────────────────────────────────────────
  async function addReportAt(lat, lng) {
    const type = window.prompt('제보 유형을 입력하세요 (예: 공사중, 급경사, 계단, 빙판)', '공사중')
    if (type === null) return
    const desc = window.prompt('상세 내용을 입력하세요', '') || ''
    try {
      const created = await postReport({ lat, lng, type: type || '기타', desc })
      setReports((prev) => [...prev, created])
      showToast('제보가 등록되었습니다.')
    } catch {
      showToast('제보 등록에 실패했습니다.')
    }
  }

  // 위험 제보 모달 제출 (미리보기 지도에서 고른 위치에 제보)
  async function submitReport() {
    const lat = reportLoc?.lat ?? CENTER.lat
    const lng = reportLoc?.lng ?? CENTER.lng
    try {
      const created = await postReport({ lat, lng, type: reportType, desc: reportDesc })
      setReports((prev) => [...prev, created])
      setReportOpen(false)
      setReportDesc('')
      showToast('등록되었습니다')
    } catch {
      showToast('제보 등록에 실패했습니다.')
    }
  }

  // ── 장소 검색 ────────────────────────────────────────────────
  function handleSearch(e) {
    e.preventDefault()
    if (!keyword.trim() || !placesRef.current) return
    placesRef.current.keywordSearch(
      keyword,
      (data, status) => {
        const { kakao } = window
        if (status === kakao.maps.services.Status.OK) {
          setSearchResults(data.slice(0, 6))
        } else {
          setSearchResults([])
          showToast('검색 결과가 없습니다.')
        }
      },
      { location: new window.kakao.maps.LatLng(CENTER.lat, CENTER.lng), radius: 5000 },
    )
  }

  function applySearch(place, target) {
    const lat = parseFloat(place.y)
    const lng = parseFloat(place.x)
    if (target === 'orig') {
      setOrig({ lat, lng })
      setOrigLabel(place.place_name)
    } else {
      setDest({ lat, lng })
      setDestLabel(place.place_name)
    }
    mapRef.current?.panTo(new window.kakao.maps.LatLng(lat, lng))
  }

  // 안내 시작 버튼: 진행 중이면 중지, 악천후면 날씨 팝업 먼저, 아니면 바로 시작
  function handleStartClick() {
    if (navRunning) {
      stopNavigation()
      return
    }
    if (weather?.alert) {
      setWeatherPopup(true)
      return
    }
    startNavigation()
  }

  // ── 안내 시뮬레이션 ──────────────────────────────────────────
  function startNavigation() {
    const r = routes?.[selected]
    if (!r || !r.coords || r.coords.length < 2) {
      showToast('먼저 경로를 선택하세요.')
      return
    }
    stopNavigation()
    const { kakao } = window
    const points = densify(r.coords, 12)
    const warned = new Set()
    let i = 0

    const marker = new kakao.maps.Marker({
      position: new kakao.maps.LatLng(points[0][0], points[0][1]),
      map: mapRef.current,
      image: pinImage('#111827', '▶'),
    })
    navMarkerRef.current = marker
    setNavRunning(true)

    navTimerRef.current = window.setInterval(() => {
      i += 1
      if (i >= points.length) {
        showToast('🏁 목적지에 도착했습니다.')
        stopNavigation()
        return
      }
      const [lat, lng] = points[i]
      marker.setPosition(new kakao.maps.LatLng(lat, lng))
      // 제보 근접 경고
      for (const rep of reports) {
        const d = haversine(lat, lng, rep.lat, rep.lng)
        if (d <= ALERT_RADIUS && !warned.has(rep.id)) {
          warned.add(rep.id)
          showToast(`⚠ 위험 구간 접근: ${rep.type} — ${rep.desc || ''} (${Math.round(d)}m)`, 5000)
        }
      }
    }, 250)
  }

  function stopNavigation() {
    if (navTimerRef.current) {
      window.clearInterval(navTimerRef.current)
      navTimerRef.current = null
    }
    navMarkerRef.current?.setMap(null)
    navMarkerRef.current = null
    setNavRunning(false)
  }

  useEffect(() => () => stopNavigation(), [])

  // 제보 모달: 위치 선택용 미리보기 지도 (가운데 고정 핀 = 선택 위치)
  useEffect(() => {
    if (!reportOpen || !ready) return
    const { kakao } = window
    const base =
      mapRef.current?.getCenter() || new kakao.maps.LatLng(CENTER.lat, CENTER.lng)
    setReportLoc({ lat: base.getLat(), lng: base.getLng() })
    const t = setTimeout(() => {
      if (!reportMapEl.current) return
      const m = new kakao.maps.Map(reportMapEl.current, { center: base, level: 3 })
      reportMapRef.current = m
      kakao.maps.event.addListener(m, 'idle', () => {
        const c = m.getCenter()
        setReportLoc({ lat: c.getLat(), lng: c.getLng() })
      })
    }, 60)
    return () => {
      clearTimeout(t)
      reportMapRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportOpen, ready])

  // 세 경로가 모두 동일하면 '최적 경로' 하나로 합쳐 보여준다
  const validKeys = routes
    ? STRATEGY_ORDER.filter((k) => routes[k]?.coords?.length >= 2)
    : []
  const routesAllSame =
    validKeys.length >= 1 &&
    new Set(validKeys.map((k) => JSON.stringify(routes[k].coords))).size === 1

  // ── 렌더 ─────────────────────────────────────────────────────
  return (
    <div className="app">
      <header className="appbar">
        <div className="brand">
          <img className="brand-logo" src="/app-icon-v4.png" alt="가자" />
          <div>
            <div className="brand-name">가자</div>
            <div className="brand-sub">가는 길 자동추천, 가는 길에 자신감</div>
          </div>
        </div>
        <button className="report-btn" onClick={() => setReportOpen(true)}>
          ⚠ 위험 제보
        </button>
      </header>

      <div className="body">
      <aside className={`panel ${sheetExpanded ? 'expanded' : ''}`}>
        <button
          className="sheet-handle"
          onClick={() => setSheetExpanded((v) => !v)}
          aria-label={sheetExpanded ? '패널 접기' : '패널 펼치기'}
        />
        <div className="tabs">
          <button
            className={`tab ${panelStep === 'input' ? 'on' : ''}`}
            onClick={() => setPanelStep('input')}
          >
            경로 설정
          </button>
          <button
            className={`tab ${panelStep === 'results' ? 'on' : ''}`}
            onClick={() => routes && setPanelStep('results')}
            disabled={!routes}
          >
            안내 결과
          </button>
        </div>
        {panelStep === 'input' && (
        <>
        <section className="block">
          <h2 className="sec-title">
            <span className="sec-icon pin">📍</span> 출발지 · 도착지
          </h2>
          <div
            className={`od-field ${mode === 'orig' ? 'active' : ''}`}
            onClick={() => setMode('orig')}
          >
            <span className="od-tag start">출발</span>
            <span className={`od-val ${origLabel ? '' : 'ph'}`}>
              {origLabel || '현재 위치로 설정'}
            </span>
          </div>
          <button
            className="od-swap"
            title="출발 · 도착 바꾸기"
            onClick={() => {
              if (orig && dest) {
                setOrig(dest)
                setDest(orig)
                setOrigLabel(destLabel)
                setDestLabel(origLabel)
              }
            }}
          >
            ↑↓
          </button>
          <div
            className={`od-field ${mode === 'dest' ? 'active' : ''}`}
            onClick={() => setMode('dest')}
          >
            <span className="od-tag end">도착</span>
            <span className={`od-val ${destLabel ? '' : 'ph'}`}>
              {destLabel || '도착지 선택'}
            </span>
          </div>
          <p className="hint">
            칸을 누른 뒤 지도를 클릭하면 <b>{mode === 'orig' ? '출발지' : mode === 'dest' ? '도착지' : '제보 위치'}</b>로
            지정됩니다.
          </p>

          <form className="search" onSubmit={handleSearch}>
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="장소 검색 (예: 성신여대입구역)"
            />
            <button type="submit">검색</button>
          </form>
          {searchResults.length > 0 && (
            <ul className="results">
              {searchResults.map((p) => (
                <li key={p.id}>
                  <div className="r-name">{p.place_name}</div>
                  <div className="r-addr">{p.road_address_name || p.address_name}</div>
                  <div className="r-btns">
                    <button onClick={() => applySearch(p, 'orig')}>출발</button>
                    <button onClick={() => applySearch(p, 'dest')}>도착</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="block">
          <h2 className="sec-title">
            <span className="sec-icon">👤</span> 사용자 유형
          </h2>
          <div className="ut-chips">
            {USER_TYPES.map((u) => (
              <button
                key={u.key}
                className={`ut-chip ${userType === u.key ? 'on' : ''}`}
                onClick={() => setUserType(u.key)}
              >
                {u.icon && <span className="ut-chip-ic">{u.icon}</span>}
                {u.label}
              </button>
            ))}
          </div>
        </section>

        <section className="block">
          <h2 className="sec-title">
            <span className="sec-icon">🌧️</span> 날씨 반영
          </h2>
          {weather ? (
            <div className="weather-live">
              <div className="wl-main">
                <span className="wl-badge">실시간</span>
                <b>{weather.condition}</b> · {weather.temp}°C
                {weather.precip ? ` · 강수 ${weather.precip}mm` : ''}
              </div>
              <div className="wl-note">{weather.note}</div>
            </div>
          ) : (
            <div className="wl-note">날씨 정보를 불러오는 중…</div>
          )}
        </section>

        <section className="block">
          <button className="primary" onClick={handleFindRoute} disabled={loading}>
            {loading ? '경로 계산 중…' : '경로 찾기'}
          </button>
          {apiError && <p className="err">⚠ {apiError}</p>}
        </section>
        </>
        )}

        {panelStep === 'results' && routes && (
        <>
          <div className="results-head">
            <h1 className="rh-title">추천 경로 3가지</h1>
            <p className="rh-sub">
              {USER_TYPES.find((u) => u.key === userType)?.label}님께 맞춰 안내해요
            </p>
          </div>

          <RouteProfile route={routes[selected]} />

          {(recLoading || recommendation) && (
            <div className="ai-card">
              <div className="ai-head">
                🤖 AI 추천
                {recommendation?.source && (
                  <span className="ai-src">
                    {recommendation.source === 'gemini' ? 'Gemini' : '규칙 기반'}
                  </span>
                )}
              </div>
              {recLoading ? (
                <div className="ai-loading">AI가 경로 데이터를 분석 중…</div>
              ) : (
                recommendation && <div className="ai-reason">{recommendation.reason}</div>
              )}
            </div>
          )}

          <div className="cards">
            {routesAllSame ? (() => {
              const r = routes[validKeys[0]]
              const chips = featureChips(r.features)
              return (
                <div className="rcard sel">
                  <div className="rcard-head">
                    <span className="rcard-icon">🏅</span>
                    <b className="rcard-title">최적 경로</b>
                    <span className="rcard-check">✓</span>
                  </div>
                  <div className="rcard-meta">
                    <span>⏱ {Math.round(r.minutes)}분</span>
                    <span>📍 {(r.distance_m / 1000).toFixed(1)}km</span>
                  </div>
                  <div className="rcard-chips">
                    {chips.map((c, i) => (
                      <span key={i} className={`chip-pill ${c.danger ? 'danger' : ''}`}>
                        {c.text}
                      </span>
                    ))}
                  </div>
                  <div className="rcard-desc">
                    안전·균형·빠름 경로가 모두 같아 최적 경로 하나로 안내해요
                  </div>
                  {r.warning && <div className="card-warn">⚠ {r.warning}</div>}
                </div>
              )
            })() : STRATEGY_ORDER.map((key) => {
              const r = routes[key]
              if (!r) return null
              const meta = STRATEGY_META[key]
              const valid = r.coords && r.coords.length >= 2
              const chips = featureChips(r.features)
              return (
                <div
                  key={key}
                  className={`rcard ${selected === key ? 'sel' : ''} ${valid ? '' : 'invalid'}`}
                  onClick={() => valid && setSelected(key)}
                >
                  <div className="rcard-head">
                    <span className="rcard-icon">{meta.icon}</span>
                    <b className="rcard-title">{meta.title}</b>
                    {recommendation?.recommended === key && (
                      <span className="ai-badge">AI 추천</span>
                    )}
                    {selected === key && <span className="rcard-check">✓</span>}
                  </div>
                  {valid ? (
                    <>
                      <div className="rcard-meta">
                        <span>⏱ {Math.round(r.minutes)}분</span>
                        <span>📍 {(r.distance_m / 1000).toFixed(1)}km</span>
                      </div>
                      <div className="rcard-chips">
                        {chips.map((c, i) => (
                          <span key={i} className={`chip-pill ${c.danger ? 'danger' : ''}`}>
                            {c.text}
                          </span>
                        ))}
                      </div>
                      <div className="rcard-desc">{meta.desc}</div>
                    </>
                  ) : (
                    <div className="rcard-desc">경로를 찾지 못했어요</div>
                  )}
                  {r.warning && <div className="card-warn">⚠ {r.warning}</div>}
                  {recommendation?.notes?.[key] && (
                    <div className="card-note">🤖 {recommendation.notes[key]}</div>
                  )}
                </div>
              )
            })}
          </div>

          <div className="results-footer">
            <button
              className={`primary-cta ${navRunning ? 'stop' : ''}`}
              onClick={handleStartClick}
              disabled={!(routes[selected]?.coords?.length >= 2)}
            >
              {navRunning ? '■ 안내 중지' : '이 경로로 안내 시작'}
            </button>
          </div>
        </>
        )}
      </aside>

      <main className="map-wrap">
        {kakaoError && <div className="map-error">{kakaoError}</div>}
        <div ref={mapEl} className="map" />
        <div className="layer-control">
          <div className="lc-title">레이어</div>
          <label className="lc-row">
            <input
              type="checkbox"
              checked={showStairs}
              onChange={(e) => setShowStairs(e.target.checked)}
            />
            <span className="lc-bar" style={{ background: '#E24B4A' }} />
            계단{stairs.length ? ` (${stairs.length})` : ''}
          </label>
          <label className="lc-row">
            <input
              type="checkbox"
              checked={showReports}
              onChange={(e) => setShowReports(e.target.checked)}
            />
            <span className="lc-dot" style={{ background: '#9333EA' }} />
            제보 ({reports.length})
          </label>
        </div>
      </main>
      </div>

      {toast && <div className="toast">{toast}</div>}

      {reportOpen && (
        <div className="wm-backdrop" onClick={() => setReportOpen(false)}>
          <div className="report-modal" onClick={(e) => e.stopPropagation()}>
            <div className="rm-title">위험 구간 제보</div>
            <div className="rm-sub">다른 이용자를 위해 알려주세요</div>
            <div className="rm-q">어디에 위험이 있나요?</div>
            <div className="rm-map-wrap">
              <div ref={reportMapEl} className="rm-map" />
              <div className="rm-pin">📍</div>
            </div>
            <div className="rm-loc-hint">지도를 움직여 위험 위치를 가운데 핀에 맞춰주세요</div>
            <div className="rm-q">어떤 위험이 있나요?</div>
            <div className="rm-options">
              {REPORT_TYPES.map((t) => (
                <button
                  key={t.key}
                  className={`rm-opt ${reportType === t.key ? 'on' : ''}`}
                  onClick={() => setReportType(t.key)}
                >
                  <span className="rm-opt-ic">{t.icon}</span>
                  <span className="rm-opt-label">{t.key}</span>
                </button>
              ))}
            </div>
            <div className="rm-q">자세한 내용 (선택)</div>
            <textarea
              className="rm-desc"
              value={reportDesc}
              onChange={(e) => setReportDesc(e.target.value)}
              placeholder="예: 빗물이 고여 있어서 미끄러워요"
            />
            <button className="rm-submit" onClick={submitReport}>
              제보하기
            </button>
          </div>
        </div>
      )}

      {weatherPopup && weather && (
        <div className="wm-backdrop" onClick={() => setWeatherPopup(false)}>
          <div className="weather-modal" onClick={(e) => e.stopPropagation()}>
            <div className="wm-emoji">🌧️</div>
            <div className="wm-title">잠깐, {weather.condition} 발생!</div>
            <div className="wm-body">
              지금 {weather.state || weather.condition} 상태에요~
              <br />
              가시는 구간에 경사로가 있으니 주의하세요!
            </div>
            <div className="wm-btns">
              <button
                className="wm-sub"
                onClick={() => {
                  setWeatherPopup(false)
                  setPanelStep('results')
                  setSheetExpanded(true)
                }}
              >
                경로 재설정
              </button>
              <button
                className="wm-main"
                onClick={() => {
                  setWeatherPopup(false)
                  startNavigation()
                }}
              >
                확인했어요
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── 마커 핀 이미지 (SVG data URI) ──────────────────────────────
function pinImage(color, text) {
  const { kakao } = window
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="32" height="40" viewBox="0 0 32 40">
      <path d="M16 0C7.2 0 0 7.2 0 16c0 11 16 24 16 24s16-13 16-24C32 7.2 24.8 0 16 0z" fill="${color}"/>
      <circle cx="16" cy="16" r="10" fill="#fff"/>
      <text x="16" y="21" font-size="12" font-weight="bold" text-anchor="middle" fill="${color}">${text}</text>
    </svg>`
  const url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
  return new kakao.maps.MarkerImage(url, new kakao.maps.Size(32, 40), {
    offset: new kakao.maps.Point(16, 40),
  })
}

// 계단 아이콘 (작은 빨강 배지에 계단 모양)
function stairsIcon() {
  const { kakao } = window
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">
      <rect x="1" y="1" width="20" height="20" rx="5" fill="#E24B4A" stroke="#fff" stroke-width="1.5"/>
      <path d="M4 16 H8 V13 H12 V10 H16 V7" stroke="#fff" stroke-width="1.8" fill="none"
            stroke-linecap="square" stroke-linejoin="miter"/>
    </svg>`
  const url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
  return new kakao.maps.MarkerImage(url, new kakao.maps.Size(22, 22), {
    offset: new kakao.maps.Point(11, 11),
  })
}

function fmtPoint(p) {
  return `${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`
}

// 경로 특성 → 칩 목록 (계단/경사/거친노면)
function featureChips(f = {}) {
  const chips = [
    { text: f.stairs ? `계단 ${f.stairs}곳` : '계단 없음', danger: f.stairs > 0 },
    { text: f.steep ? `경사 ${f.steep}곳` : '경사 완만', danger: f.steep > 0 },
  ]
  if (f.rough) chips.push({ text: `거친노면 ${f.rough}곳`, danger: true })
  return chips
}

// 선택 경로의 경사/위험 프로필 그래픽 (출발→도착, 구간 경사로 색 분류)
function RouteProfile({ route }) {
  if (!route || !route.coords || route.coords.length < 2) {
    return <div className="profile-card empty">경로 미리보기를 표시할 수 없어요</div>
  }
  const grades =
    route.segment_grades && route.segment_grades.length ? route.segment_grades : [0]
  const W = 300
  const H = 150
  const padX = 28
  const padTop = 28
  const padBot = 34
  // 누적 경사로 의사 고도 프로필(왼쪽 출발 → 오른쪽 도착으로 상승)
  const cum = [0]
  for (let i = 0; i < grades.length; i++) cum.push(cum[i] + grades[i])
  const maxCum = Math.max(cum[cum.length - 1], 0.0001)
  const n = cum.length
  const xAt = (i) => padX + (i / (n - 1)) * (W - 2 * padX)
  const yAt = (v) => H - padBot - (v / maxCum) * (H - padTop - padBot)
  const pts = cum.map((v, i) => [xAt(i), yAt(v)])
  return (
    <div className="profile-card">
      <div className="profile-legend">
        <span>
          <i style={{ background: '#1D9E75' }} />안전
        </span>
        <span>
          <i style={{ background: '#EF9F27' }} />주의
        </span>
        <span>
          <i style={{ background: '#E24B4A' }} />위험
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="profile-svg">
        {grades.map((g, i) => (
          <line
            key={i}
            x1={pts[i][0]}
            y1={pts[i][1]}
            x2={pts[i + 1][0]}
            y2={pts[i + 1][1]}
            stroke={routeSlopeColor(g)}
            strokeWidth="4"
            strokeLinecap="round"
          />
        ))}
        <circle cx={pts[0][0]} cy={pts[0][1]} r="6" fill="#1D9E75" />
        <circle cx={pts[n - 1][0]} cy={pts[n - 1][1]} r="6" fill="#E24B4A" />
        <text x={pts[0][0]} y={H - 12} fontSize="11" fill="#7b8794" textAnchor="middle">
          출발
        </text>
        <text
          x={pts[n - 1][0]}
          y={Math.max(pts[n - 1][1] - 12, 14)}
          fontSize="11"
          fill="#7b8794"
          textAnchor="middle"
        >
          도착
        </text>
      </svg>
    </div>
  )
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
}
