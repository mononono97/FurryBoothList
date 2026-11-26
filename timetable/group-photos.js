// 행사 시간표 탭 > "단체촬영" 화면에 보이는 소규모 단체촬영 일정.
// 원본: 행사 참가자들이 함께 쓰는 단체촬영 신청 시트(구글 스프레드시트). 시트가 바뀌면 이 파일만 고치면 됨.
// 실시간으로 시트를 읽지 않고 이렇게 정적 파일로 두는 이유: 브라우저에서 구글 시트를 직접 읽으면 권한·CORS 문제가 있고,
//   행사장 회선이 느리거나 끊겨도(오프라인) 보이게 하려면 사이트 파일 안에 있어야 함.
// 항목 형식 (빈 칸은 "" 로 두거나 줄을 지우면 됨):
//   day   : "sat"(토 11/28) 또는 "sun"(일 11/29)
//   start : 시작 시각 "HH:MM" (24시간제)   end : 끝 시각 "HH:MM" (모르면 "")
//   place : 장소              title : 촬영 주제(단체 이름)
//   host  : 주최자(X 아이디는 @ 포함)       note : 참고 사항(복장, 인원 등)
//   link  : 안내 글이나 신청 링크(https://...) 없으면 ""
// 같은 요일 안에서는 시작 시각 순으로 자동 정렬됨.
// 시트의 "예상 소요시간"(모두 30분)으로 끝 시각을 계산해 넣음. 시트의 "ex)" 예시 줄은 뺌.
// 촬영 장소는 시트에 적힌 기호(A·B) 그대로.
window.GROUP_PHOTO_UPDATED = "2026-11-26";
// 요일별 촬영 가능 시간(시트 아래 안내 문구)
window.GROUP_PHOTO_HOURS = { sat: "11:00~17:00", sun: "12:00~16:00" };
window.GROUP_PHOTOS = [
  { day: "sat", start: "12:30", end: "13:00", place: "A", title: "조류 단체촬영", host: "TAN", note: "", link: "" },
  { day: "sat", start: "12:30", end: "13:00", place: "A", title: "Cangpai 패밀리 단체촬영", host: "Chen", note: "", link: "" },
  { day: "sat", start: "13:00", end: "13:30", place: "A", title: "털털엔지니어링 단체촬영", host: "벅벅", note: "1시 10분 1차 셔터 / 1시 20분 2차 셔터", link: "" },
  { day: "sat", start: "13:00", end: "13:30", place: "A", title: "블루폭스(Blue Fox) 퍼슈트 단체촬영", host: "트링", note: "1시 15분 셔터", link: "" },
  { day: "sat", start: "13:30", end: "14:00", place: "A", title: "여퍼만 / 비메이커 촬영", host: "김루미", note: "1시 45분 셔터", link: "" },
  { day: "sat", start: "14:00", end: "14:30", place: "A", title: "실버코인 단체촬영", host: "남랑", note: "2시 20분 셔터", link: "" },
  { day: "sat", start: "14:30", end: "15:00", place: "A", title: "꿀떡이네 단체촬영", host: "댕꿀떡", note: "", link: "" },
  { day: "sat", start: "15:30", end: "16:00", place: "A", title: "BPC 패밀리 단체촬영", host: "해견", note: "", link: "" },
  { day: "sat", start: "16:00", end: "16:30", place: "A", title: "쉐도위 퍼슈트 단체촬영", host: "럭르즈", note: "4시 20분 셔터", link: "" },
  { day: "sat", start: "16:30", end: "17:00", place: "", title: "우싸털가 단체촬영", host: "오백비", note: "4시 50분 셔터", link: "" },
  { day: "sun", start: "13:00", end: "13:30", place: "B", title: "GPS 단체 촬영", host: "켄 KEN", note: "1시 15분 셔터", link: "" },
  { day: "sun", start: "14:00", end: "14:30", place: "B", title: "BINARI 메이커촬", host: "하울프", note: "2시 10분 셔터", link: "" },
  { day: "sun", start: "14:30", end: "15:00", place: "B", title: "개인 단촬", host: "페브르", note: "2시 40분 셔터", link: "" },
  { day: "sun", start: "15:00", end: "15:30", place: "B", title: "퍼슈트도서관 단체 촬영", host: "남랑", note: "3시 20분 셔터", link: "" },
  { day: "sun", start: "15:30", end: "16:00", place: "B", title: "리얼계 단체 촬영", host: "Infernia / WHISKY", note: "3시 40분 셔터", link: "" },
];
