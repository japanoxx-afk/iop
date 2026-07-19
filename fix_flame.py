# -*- coding: utf-8 -*-
"""
화염병 데미지 미적용 수정 도구
 - 화염병 무기(id7)가 특수 화염방식이라 데미지가 안 들어가는 문제를 고침.

사용:
  python fix_flame.py flag   : 최소수정 - 데미지 플래그(0x28의 0x20000비트)만 추가.
                               화염 연출 유지. 먼저 이걸 시도해보고 게임에서 확인.
  python fix_flame.py swap   : 확실한 수정 - 정상 작동하는 소총병 무기 방식으로 교체.
                               공격력/사거리/지상전용은 유지, 발사체는 일반탄으로 바뀜.
  python fix_flame.py restore: 화염병 무기를 원본으로 되돌림.
"""
import struct, os, sys, shutil

GAME = r"C:\Users\seo\Downloads\DGGL\Games\IOP_Win\data"
BAK  = r"C:\Users\seo\Downloads\DGGL\Games\IOP_Win\data_backup_original"
FLAME_WID = 7      # 화염병 무기 id
DONOR_WID = 4      # 소총병 무기 id (정상 작동)

def load(path):
    data = bytearray(open(path, "rb").read())
    n = struct.unpack_from("<I", data, 0)[0]
    idx = [struct.unpack_from("<HHI", data, 4+i*8) for i in range(n)]
    order = sorted(range(n), key=lambda k: idx[k][2])
    bounds = {}
    for oi,k in enumerate(order):
        uid,typ,off = idx[k]
        end = idx[order[oi+1]][2] if oi+1<len(order) else len(data)
        if (typ>>8)&0xFF == 0x07:
            bounds[uid] = (off, end)
    return data, bounds

def ensure_backup():
    if not os.path.isdir(BAK):
        os.makedirs(BAK)
    bp = os.path.join(BAK, "Gweapon.res")
    if not os.path.exists(bp):
        shutil.copy(os.path.join(GAME, "Gweapon.res"), bp)
        print("원본 백업 생성:", bp)

def main(mode):
    pwpn = os.path.join(GAME, "Gweapon.res")
    ensure_backup()
    data, bounds = load(pwpn)
    fo, fe = bounds[FLAME_WID]

    if mode == "flag":
        v = struct.unpack_from("<I", data, fo + 0x28)[0]
        newv = v | 0x20000
        struct.pack_into("<I", data, fo + 0x28, newv)
        open(pwpn, "wb").write(data)
        print(f"[flag] 화염병 무기 0x28: {v:#010x} -> {newv:#010x} (데미지 플래그 추가)")
        print("게임에서 화염병으로 적을 공격해 데미지가 들어가는지 확인하세요.")

    elif mode == "swap":
        do, de = bounds[DONOR_WID]
        donor = bytes(data[do:de])
        # 화염병 고유값 보존
        keep_idtype = bytes(data[fo:fo+4])        # 0x00-0x03 id/type
        keep_atk = struct.unpack_from("<I", data, fo + 0x04)[0]
        keep_rng = struct.unpack_from("<I", data, fo + 0x19)[0]
        keep_tgt = struct.unpack_from("<I", data, fo + 0x2c)[0]  # 지상전용 유지
        # 레코드 길이가 같다고 가정(둘 다 56)
        L = min(fe - fo, len(donor))
        data[fo:fo+L] = donor[:L]
        # 보존값 복원
        data[fo:fo+4] = keep_idtype
        struct.pack_into("<I", data, fo + 0x04, keep_atk)
        struct.pack_into("<I", data, fo + 0x19, keep_rng)
        struct.pack_into("<I", data, fo + 0x2c, keep_tgt)
        open(pwpn, "wb").write(data)
        print(f"[swap] 화염병 무기를 소총병 방식으로 교체 (공격력 {keep_atk}, 사거리 유지, 지상전용)")
        print("게임에서 데미지가 들어가는지 확인하세요. (발사체가 일반탄으로 보일 수 있음)")

    elif mode == "restore":
        src = os.path.join(BAK, "Gweapon.res")
        if not os.path.exists(src):
            print("백업이 없습니다."); return
        # 화염병 무기 레코드만 원본에서 복원
        odata, obounds = load(src)
        oo, oe = obounds[FLAME_WID]
        data[fo:fe] = odata[oo:oe]
        open(pwpn, "wb").write(data)
        print("화염병 무기를 원본으로 복원했습니다.")
    else:
        print(__doc__)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
