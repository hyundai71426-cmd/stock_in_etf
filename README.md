# ETF 구성종목 검색 (한국 ETF)

종목명/코드로 검색하면 KRX 데이터 기준 ETF 구성종목을 보여주는 웹서비스입니다.

## 구조
- `index.html` — 검색 UI (정적 파일)
- `api/search.py` — 종목명/코드로 ETF 찾기 (`GET /api/search?q=검색어`)
- `api/holdings.py` — ETF 구성종목 조회 (`GET /api/holdings?isin=ISIN코드`)

데이터는 KRX 정보데이터시스템(data.krx.co.kr)의 공개 API를 서버에서 호출합니다. 회원가입이나 API 키가 필요 없습니다.

## 로컬 실행
```bash
cd webapp
pip install -r requirements.txt
npm install -g vercel   # 최초 1회
vercel dev
```
브라우저에서 `http://localhost:3000` 접속.

## 무료 배포 (Vercel)
1. [vercel.com](https://vercel.com) 가입 (GitHub 계정으로 가능)
2. 이 `webapp` 폴더를 GitHub 저장소에 올리기
3. Vercel에서 "Add New Project" → 해당 저장소 선택 → Deploy
   - Framework Preset: Other
   - Build/Output 설정은 `vercel.json`이 자동으로 처리
4. 배포 완료 후 `https://프로젝트명.vercel.app` 주소로 바로 접속 가능

또는 CLI로 바로 배포:
```bash
cd webapp
vercel --prod
```

## 참고/한계
- 휴장일에는 가장 최근 거래일 데이터를 자동으로 찾아 보여줍니다(최대 10일 전까지).
- KRX 비공개 API라 추후 응답 형식이 바뀌면 코드 수정이 필요할 수 있습니다.
- 검색 결과는 최대 30개까지 표시됩니다.
