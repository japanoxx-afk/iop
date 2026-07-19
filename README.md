# iop

임팩트 오브 파워(Impact of Power) 런처 · 밸런스 편집기

## 구성
- `iop_launcher.py` — 게임 실행(창모드/전체화면), 밸런스 편집, 네트워크(IPX/Radmin) 설정을 담은 통합 런처
- `iop_balance.py` — 밸런스 데이터(.res) 읽기/쓰기 로직
- `fix_flame.py` — 화염병 데미지 미적용 버그 수정 도구
- `1_EXPORT.bat` / `2_APPLY.bat` / `3_RESTORE.bat` — 밸런스 CSV 내보내기/적용/복구 배치
- `FLAME_1_flag.bat` / `FLAME_2_swap.bat` / `FLAME_3_restore.bat` — 화염병 수정 배치

## 빌드
```
pyinstaller --onefile --noconsole --name "임팩트오브파워런처" --add-data "iop_balance.py;." iop_launcher.py
```

빌드된 exe는 게임 폴더(iop.exe 가 있는 곳)에 두고 실행합니다.
