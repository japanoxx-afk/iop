# iop

임팩트 오브 파워(Impact of Power) 런처 · 밸런스 편집기

## 구성
- **v0.015 영상 녹화:** 게임 창 무음 자동 녹화, 프로그램 종료 시 MP4 저장, 영상 목록과 재생. [사용법과 검증 범위](VIDEO_RECORDING.md)
- **v0.012 맵 저장:** 현재 파일 덮어쓰기와 직전 백업, 다른 이름으로 저장, 타일 그림별 일반·언덕·입구 분류 지정 및 영구 보관. [사용법](MAP_EDITOR.md)
- **v0.010 맵 에디터:** 실제 타일 그래픽·통행 속성 편집, 언덕 영역 복사, 자원 추가/삭제, 빈 맵 생성, 시작 위치 편집, 실행 취소. 뷰어에 시작 위치와 자원 표시. [사용법·검증 범위·언덕 시야 검토](MAP_EDITOR.md)
- **v0.009 프리서버 통합:** A PC Radmin IP 자동·영구 저장, 단순화된 탭 구성, 자동 업데이트 교체·재시작 수정, 생산 단축키 영구 적용, 멀티플레이 맵 뷰어, 서버 ON/OFF, 창모드 기본, 진단 로그/ZIP 저장, hosts 자동 등록, 화염병 수정 및 A·B 게임 파일 비교. [A/B PC 설정 방법](PRIVATE_SERVER.md)
- `iop_maps.py`, `iop_map_tab.py` — 멀티플레이 맵 크기·인원·테마 판독과 팔레트 기반 지형 미리보기
- `iop_network.py`, `iop_server_tab.py`, `iop_admin.py`, `iop_elevation.py`, `iop_sync.py`, `iop_server/` — 내장 서버와 런처 통합 기능
- `iop_launcher.py` — 게임 실행(창모드/전체화면), 밸런스 편집, 네트워크(IPX/Radmin) 설정을 담은 통합 런처
- `iop_balance.py` — 밸런스 데이터(.res) 읽기/쓰기 로직
- `fix_flame.py` — 화염병 데미지 미적용 버그 수정 도구
- `1_EXPORT.bat` / `2_APPLY.bat` / `3_RESTORE.bat` — 밸런스 CSV 내보내기/적용/복구 배치
- `FLAME_1_flag.bat` / `FLAME_2_swap.bat` / `FLAME_3_restore.bat` — 화염병 수정 배치

## 빌드
```
pyinstaller --onefile --noconsole --name IOPLauncher_Server --distpath release --add-data "iop_balance.py;." iop_launcher.py
```

빌드된 exe는 게임 폴더(iop.exe 가 있는 곳)에 두고 실행합니다.
