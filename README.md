# iop

임팩트 오브 파워(Impact of Power) 런처 · 밸런스 편집기

## 구성
- **v0.007 프리서버 통합:** 멀티플레이 맵 뷰어, 서버 ON/OFF, GitHub SHA-256 검증 업데이트, 유닛 생산 단축키 설정, 창모드 기본, 진단 로그/ZIP 저장, 게임 시작 통일, hosts 자동 등록, 화염병 수정 및 A·B 게임 파일 비교. [A/B PC 설정 방법](PRIVATE_SERVER.md)
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
