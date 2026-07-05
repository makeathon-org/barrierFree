import { useEffect, useState } from 'react'

/**
 * 카카오맵 SDK 로딩 훅.
 * index.html 에서 autoload=false 로 스크립트를 불러오므로,
 * window.kakao 가 준비되면 kakao.maps.load() 를 호출해 초기화한다.
 */
export function useKakao() {
  const [ready, setReady] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let tries = 0
    const timer = setInterval(() => {
      tries += 1
      if (window.kakao && window.kakao.maps) {
        clearInterval(timer)
        window.kakao.maps.load(() => setReady(true))
      } else if (tries > 100) {
        // 약 10초 대기 후 실패
        clearInterval(timer)
        setError(
          '카카오맵 SDK 를 불러오지 못했습니다. .env 의 VITE_KAKAO_KEY 와 ' +
            '도메인(http://localhost:5173) 등록을 확인하세요.',
        )
      }
    }, 100)
    return () => clearInterval(timer)
  }, [])

  return { ready, error }
}
