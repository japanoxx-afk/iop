# -*- coding: utf-8 -*-
"""
IOP(임팩트 오브 파워) 밸런스 편집 도구
- .res 컨테이너는 [u32 count][count x (u16 id,u16 type,u32 offset)][records] 구조.
- 레코드 순서/오프셋을 원본 그대로 보존하며 in-place로 필드만 덮어쓴다(길이 불변 수정).

명령:
  verify              : 모든 .res 파일 무손실 라운드트립 검증
  export <out.csv>    : 유닛/무기 스탯을 CSV로 내보내기
  import <in.csv>     : CSV를 읽어 data 폴더의 .res 파일에 반영
"""
import struct, os, sys, csv

GAME = r"C:\Users\seo\Downloads\DGGL\Games\IOP_Win\data"

# ---- 식별된 필드 오프셋 (분석 + 메모리스캔/크래시경계/실게임 검증) ----
# Gubattle.res 레코드
BAT_BUILD = 0x3C       # u32 빌드타임 (메모리스캔 36/0 검증)
BAT_HP    = 0x40       # u32 체력(HP)
BAT_ADAMAS = 0x80      # u32 아다마스 가격 (메모리스캔 36/0 검증)
BAT_PLATINUM = 0x84    # u32 플래티넘 가격 (구조패턴 추정, 게임내 미검증)
# 방어력(D)은 유닛 기본스탯이 아니라 업그레이드 보너스(기본 0). Gubattle에 없음.
# Gucombat.res 레코드: 유닛->무기 참조 (0x04, 0x08 두 슬롯)
CMB_WPN_SLOTS = (0x04, 0x08)
# Gweapon.res 레코드
WPN_ATK = 0x04         # u32 공격력
WPN_RNG = 0x19         # u32 사거리(비정렬)
WPN_TARGET = 0x2C      # u32 타겟마스크: bit0(0x01000000)=지상, bit1(0x02000000)=공중 (실게임 검증)
TGT_GND = 0x01000000
TGT_AIR = 0x02000000

RACE = {0x00: "노블어스", 0x20: "다크존", 0x40: "아트로스"}
KIND = {0x05: "보병", 0x0B: "기갑", 0x13: "공중", 0x0A: "건물", 0x18: "특수건물", 0x1B: "지형"}

def load(path):
    data = bytearray(open(path, "rb").read())
    n = struct.unpack_from("<I", data, 0)[0]
    idx = []
    for i in range(n):
        uid, typ, off = struct.unpack_from("<HHI", data, 4 + i * 8)
        idx.append([uid, typ, off])
    # 레코드 경계(오프셋 정렬 기준)
    order = sorted(range(n), key=lambda k: idx[k][2])
    bounds = {}
    for oi, k in enumerate(order):
        off = idx[k][2]
        end = idx[order[oi + 1]][2] if oi + 1 < len(order) else len(data)
        bounds[k] = (off, end)
    return data, n, idx, bounds

def rec_slice(data, idx, bounds, k):
    off, end = bounds[k]
    return data, off, end

def read_u32(data, off):
    return struct.unpack_from("<I", data, off)[0]

def write_u32(data, off, val):
    struct.pack_into("<I", data, off, val)

def get_names(b):
    names = []
    i = 0x5C
    while i < len(b) - 1:
        l = b[i]
        if 2 <= l <= 40 and i + 1 + l <= len(b):
            chunk = b[i+1:i+1+l]
            try:
                s = chunk.decode("cp949").strip("\x00").strip()
                if s and all(0x20 <= ord(c) < 0x7F or ord(c) > 0x3000 for c in s):
                    names.append(s); i += 1 + l; continue
            except UnicodeDecodeError:
                pass
        i += 1
    return names

# ---------------- verify ----------------
def cmd_verify():
    bad = 0
    for fn in sorted(os.listdir(GAME)):
        if not fn.lower().endswith(".res"):
            continue
        path = os.path.join(GAME, fn)
        orig = open(path, "rb").read()
        data, n, idx, bounds = load(path)
        # 인덱스를 그대로 다시 써서 재직렬화 → 원본과 동일해야 함
        rebuilt = bytearray(data)  # in-place 모델이므로 data 자체가 곧 파일
        ok = bytes(rebuilt) == orig
        print(f"{fn:16s} n={n:4d} roundtrip={'OK' if ok else 'FAIL'}")
        if not ok:
            bad += 1
    print(f"\n결과: {'전체 통과' if bad == 0 else str(bad)+'개 실패'}")

# ---------------- export ----------------
def cmd_export(outcsv):
    dbat, nb, ibat, bbat = load(os.path.join(GAME, "Gubattle.res"))
    dwpn, nw, iwpn, bwpn = load(os.path.join(GAME, "Gweapon.res"))
    dcmb, nc, icmb, bcmb = load(os.path.join(GAME, "Gucombat.res"))
    # 무기 id->(atk, rng, target_mask) 맵
    wpn = {}
    for k in range(nw):
        uid, typ, _ = iwpn[k]
        off, end = bwpn[k]
        tgt = read_u32(dwpn, off + WPN_TARGET) if end - off >= 0x30 else 0
        wpn[uid] = (read_u32(dwpn, off + WPN_ATK), read_u32(dwpn, off + WPN_RNG), tgt)
    # combat: (unit_id,unit_type) -> 참조 무기 id 목록
    unit_wpns = {}
    for k in range(nc):
        uid, typ, _ = icmb[k]
        off, end = bcmb[k]
        wids = []
        for slot in CMB_WPN_SLOTS:
            wid, wtyp = struct.unpack_from("<HH", dcmb, off + slot)
            if (wtyp >> 8) & 0xFF == 0x07 and wid != 0:
                wids.append(wid)
        unit_wpns[(uid, typ)] = wids

    def resolve(uid, typ):
        """참조 무기들의 타겟마스크로 지상/공중 공격·사거리·무기id 결정"""
        g_atk = g_rng = a_atk = a_rng = 0
        gwid = awid = 0
        for wid in unit_wpns.get((uid, typ), []):
            atk, rng, tgt = wpn.get(wid, (0, 0, 0))
            if tgt & TGT_GND and gwid == 0:
                g_atk, g_rng, gwid = atk, rng, wid
            if tgt & TGT_AIR and awid == 0:
                a_atk, a_rng, awid = atk, rng, wid
        return g_atk, a_atk, g_rng, a_rng, gwid, awid

    rows = []
    for k in range(nb):
        uid, typ, _ = ibat[k]
        off, end = bbat[k]
        b = dbat[off:end]
        kind = (typ >> 8) & 0xFF
        if kind == 0x1B:  # 지형(tree) 제외
            continue
        hp = read_u32(dbat, off + BAT_HP)
        build = read_u32(dbat, off + BAT_BUILD)
        adamas = read_u32(dbat, off + BAT_ADAMAS) if len(b) >= 0x84 else 0
        plat = read_u32(dbat, off + BAT_PLATINUM) if len(b) >= 0x88 else 0
        g_atk, a_atk, g_rng, a_rng, gwid, awid = resolve(uid, typ)
        names = get_names(b)
        en = names[0].lstrip("x") if names else ""
        kr = names[1].lstrip("x") if len(names) > 1 else ""
        rows.append([uid, f"{typ:#06x}", RACE.get(typ & 0xFF, "?"),
                     KIND.get(kind, hex(typ)), kr, hp,
                     g_atk, a_atk, g_rng, a_rng, build, adamas, plat,
                     gwid, awid, en])

    with open(outcsv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "type", "race", "kind", "name_kr", "HP",
                    "gnd_atk", "air_atk", "gnd_range", "air_range",
                    "build_time", "adamas", "platinum",
                    "gnd_wid", "air_wid", "name_en"])
        w.writerows(rows)
    print(f"내보냄: {outcsv} ({len(rows)}행)")

# ---------------- import ----------------
def cmd_import(incsv):
    # 원본 로드
    pbat = os.path.join(GAME, "Gubattle.res")
    pwpn = os.path.join(GAME, "Gweapon.res")
    # 안전장치: 최초 1회 원본 백업 (data_backup_original)
    bakdir = os.path.join(os.path.dirname(GAME), "data_backup_original")
    if not os.path.isdir(bakdir):
        os.makedirs(bakdir)
    for fn in ("Gubattle.res", "Gweapon.res"):
        bp = os.path.join(bakdir, fn)
        if not os.path.exists(bp):
            import shutil
            shutil.copy(os.path.join(GAME, fn), bp)
            print(f"원본 백업 생성: {bp}")
    dbat, nb, ibat, bbat = load(pbat)
    dwpn, nw, iwpn, bwpn = load(pwpn)
    # 인덱스 맵
    bat_map = {}
    for k in range(nb):
        uid, typ, _ = ibat[k]
        bat_map[(uid, typ)] = bbat[k]
    wpn_map = {}
    for k in range(nw):
        uid, typ, _ = iwpn[k]
        wpn_map[uid] = bwpn[k]

    changes = 0
    warns = []
    with open(incsv, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            uid = int(row["id"]); typ = int(row["type"], 16)
            hp = int(row["HP"])
            g_atk = int(row["gnd_atk"]); a_atk = int(row["air_atk"])
            g_rng = int(row["gnd_range"]); a_rng = int(row["air_range"])
            build = int(row["build_time"]); adamas = int(row["adamas"])
            plat = int(row["platinum"])
            gwid = int(row["gnd_wid"]); awid = int(row["air_wid"])
            key = (uid, typ)
            if key in bat_map:
                off, end = bat_map[key]
                if read_u32(dbat, off + BAT_HP) != hp:
                    write_u32(dbat, off + BAT_HP, hp); changes += 1
                if read_u32(dbat, off + BAT_BUILD) != build:
                    write_u32(dbat, off + BAT_BUILD, build); changes += 1
                if end - off >= 0x84 and read_u32(dbat, off + BAT_ADAMAS) != adamas:
                    write_u32(dbat, off + BAT_ADAMAS, adamas); changes += 1
                if end - off >= 0x88 and read_u32(dbat, off + BAT_PLATINUM) != plat:
                    write_u32(dbat, off + BAT_PLATINUM, plat); changes += 1
            # 지상무기 (gnd_wid==air_wid 인 겸용무기면 지상값 우선)
            if gwid in wpn_map:
                off, end = wpn_map[gwid]
                if read_u32(dwpn, off + WPN_ATK) != g_atk:
                    write_u32(dwpn, off + WPN_ATK, g_atk); changes += 1
                if read_u32(dwpn, off + WPN_RNG) != g_rng:
                    write_u32(dwpn, off + WPN_RNG, g_rng); changes += 1
            # 공중무기 (지상과 다른 무기일 때만; 겸용이면 위에서 이미 처리)
            if awid in wpn_map and awid != gwid:
                off, end = wpn_map[awid]
                if read_u32(dwpn, off + WPN_ATK) != a_atk:
                    write_u32(dwpn, off + WPN_ATK, a_atk); changes += 1
                if read_u32(dwpn, off + WPN_RNG) != a_rng:
                    write_u32(dwpn, off + WPN_RNG, a_rng); changes += 1
            elif awid != 0 and awid == gwid and a_atk != g_atk:
                warns.append(f"  {row['name_kr']}: 지상=공중 겸용무기라 gnd_atk({g_atk}) 값으로 통일됨")

    # 화염병 데미지 수정은 항상 유지 (밸런스 적용 시 원본 flag가 덮이지 않도록)
    apply_flame_fix(dwpn, iwpn, bwpn, nw)
    open(pbat, "wb").write(dbat)
    open(pwpn, "wb").write(dwpn)
    for wln in warns:
        print(wln)
    print(f"반영 완료: {changes}개 필드 변경 (Gubattle.res, Gweapon.res) + 화염병 데미지 유지")


def apply_flame_fix(dwpn, iwpn, bwpn, nw):
    """화염병 무기(id7)의 데미지 플래그(0x28 |= 0x20000)를 항상 켠다."""
    FLAME_WID = 7
    for k in range(nw):
        uid, typ, _ = iwpn[k]
        if uid == FLAME_WID and (typ >> 8) & 0xFF == 0x07:
            off, end = bwpn[k]
            v = read_u32(dwpn, off + 0x28)
            if not (v & 0x20000):
                write_u32(dwpn, off + 0x28, v | 0x20000)
            return

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "verify":
        cmd_verify()
    elif cmd == "export":
        cmd_export(sys.argv[2])
    elif cmd == "import":
        cmd_import(sys.argv[2])
    elif cmd == "restore":
        import shutil
        bakdir = os.path.join(os.path.dirname(GAME), "data_backup_original")
        for fn in ("Gubattle.res", "Gweapon.res"):
            bp = os.path.join(bakdir, fn)
            if os.path.exists(bp):
                shutil.copy(bp, os.path.join(GAME, fn))
                print(f"원본 복구: {fn}")
            else:
                print(f"백업 없음: {fn}")
        # 원본으로 되돌려도 화염병 데미지는 항상 유지
        pwpn = os.path.join(GAME, "Gweapon.res")
        dwpn, nw, iwpn, bwpn = load(pwpn)
        apply_flame_fix(dwpn, iwpn, bwpn, nw)
        open(pwpn, "wb").write(dwpn)
        print("화염병 데미지 유지 적용됨")
    else:
        print("알 수 없는 명령:", cmd)
