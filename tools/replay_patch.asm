; IOP replay recorder / player, assembled into the ".iopr" PE section of iop.exe.
; Build: python tools/build_replay_patch.py  (nasm -f bin) -> iop_replay_code.py
;
; Game facts (original v0.019 iop.exe, no ASLR), see REPLAY_RESEARCH.md:
;   tick              [0x6C946C]           ms base [0x6C9464]
;   game mode byte    [0x4E53CC]           0 campaign, 1 skirmish, 2 network
;   setup block       0x6C92B0..0x6C9460   players/types/races/teams/names/seed/map path
;   team manager      [0x508300]           +5 player count, +9 player objects[]
;   command executor  0x435C50 (thiscall player, packet 0x6F bytes) ret 4
;   tick commands     0x435B20 (thiscall manager) runs queued commands <= tick
;   builders          0x4055E0 skirmish, 0x48F0A0 network (no args)
;   player leave      0x434060 (thiscall player, a1, a2) ret 8
;   CRT rand state    getptd 0x4BD603 -> +0x14   (per thread, main thread = sim)
;   game speed        0x4B56B0(ticks per second), [0x4DE0F8]
;   lock              CRITICAL_SECTION 0x4E23C8 (held by the command executor)

BITS 32
%define SEC        0x784000
ORG SEC

%define CTRL       (SEC + 0x1000)
; ---- launcher <-> game control block (stable addresses, read/written by the launcher)
%define C_MAGIC    (CTRL + 0x00)
%define C_VERSION  (CTRL + 0x04)
%define C_MODE     (CTRL + 0x08)     ; 0 record, 1 playback (set in the replay exe copy)
%define C_PAUSED   (CTRL + 0x0C)
%define C_SPEED    (CTRL + 0x10)     ; requested ticks/second, 0 = keep
%define C_VISION   (CTRL + 0x14)     ; 0 recorder, 1 everyone, 2+k player k
%define C_STEP     (CTRL + 0x18)     ; ticks to advance while paused
%define C_TICK     (CTRL + 0x20)
%define C_ENDTICK  (CTRL + 0x24)
%define C_DESYNC   (CTRL + 0x28)
%define C_FIRSTBAD (CTRL + 0x2C)
%define C_ENDED    (CTRL + 0x30)
%define C_REC      (CTRL + 0x34)     ; 1 while a record file is open
%define C_CMDS     (CTRL + 0x38)
%define C_STARTED  (CTRL + 0x3C)     ; playback battle entered
%define C_RANDFIX  (CTRL + 0x40)     ; rand differed but resources matched (UI sounds etc.)
%define C_NOREC    (CTRL + 0x44)     ; 1 = launcher turned recording off (set in the file)
; ---- private state
%define V_HANDLE   (CTRL + 0x60)
%define V_BUFLEN   (CTRL + 0x64)
%define V_TSF      (CTRL + 0x68)
%define V_INEXEC   (CTRL + 0x6C)
%define V_RANDPRE  (CTRL + 0x70)
%define V_PBASE    (CTRL + 0x74)
%define V_PPTR     (CTRL + 0x78)
%define V_PEND     (CTRL + 0x7C)
%define V_LASTVIS  (CTRL + 0x80)
%define V_TMPW     (CTRL + 0x84)
%define V_BEST     (CTRL + 0x88)
%define V_BESTD    (CTRL + 0x8C)
%define V_RX       (CTRL + 0x90)
%define V_RY       (CTRL + 0x94)
%define V_NAME     (CTRL + 0x100)    ; 64 bytes record file name
%define V_TMP      (CTRL + 0x180)    ; 0x100 bytes scratch record
%define BUF        (SEC + 0x2000)
%define BUF_SIZE   0x10000
%define HDR_SIZE   0x200
%define BLOCK      0x6C92B0
%define BLOCK_LEN  0x1B0

; header offsets
%define H_MAGIC    0x00
%define H_VERSION  0x04
%define H_MODE     0x08
%define H_RANDPRE  0x0C
%define H_RANDPOST 0x10
%define H_LOCALID  0x14
%define H_TPS      0x18
%define H_LOCALIDX 0x1C
%define H_STICK    0x20
%define H_BLOCK    0x40

; records
%define R_TICK     1        ; u8, u32 tick, u32 rand, u32 resource hash 13
%define R_CMD      2        ; u8, u8 player, 0x6F packet                0x71
%define R_LEAVE    3        ; u8, u8 player, u32 a1, u32 a2, u32 tick   14
%define R_END      4        ; u8, u32 tick                              5

; imports
%define CreateFileA       dword [0x4C80C0]
%define WriteFile         dword [0x4C809C]
%define ReadFile          dword [0x4C80C4]
%define CloseHandle       dword [0x4C80CC]
%define GetFileSize       dword [0x4C8098]
%define GetTickCount      dword [0x4C81E0]
%define EnterCS           dword [0x4C80B4]
%define LeaveCS           dword [0x4C80B8]
%define ExitProcess       dword [0x4C807C]
%define Sleep             dword [0x4C80AC]
%define MessageBoxA       dword [0x4C827C]
%define TimeGetTime       dword [0x4C8298]
%define GAME_CS           0x4E23C8

; ---------------------------------------------------------------- entry table
; Every hook site calls/jumps to SEC + 5*n; the order is part of the patch ABI.
entry_table:
    jmp near prebuild1      ; 0  H1  0x4059F3 call 0x4055E0
    jmp near prebuild2      ; 1  H2  0x48F3D9 call 0x48F0A0
    jmp near setup1         ; 2  H3  0x404E51 call 0x4058B0
    jmp near postsetup      ; 3  H4  0x404EAC call 0x405CE0
    jmp near tickadv        ; 4  H5  0x4044F7 call 0x48E990
    jmp near tickexec       ; 5  H6  0x404510 call 0x435B20
    jmp near cmdexec        ; 6  H7  0x435BEB call 0x435C50
    jmp near leave          ; 7  H8  0x434060 jmp (entry)
    jmp near menu           ; 8  H9  0x403F4B call + nop
    jmp near battleend      ; 9  H10 0x404783 call 0x405F30
    jmp near vision         ; 10 H11 0x42DF95 call (replaces cmp)
    jmp near control        ; 11 H12 0x4247D0 jmp (is player controllable)
    jmp near acc78          ; 12 H13 0x4495C0 jmp (unit order, vt+0x78)
    jmp near acc7c          ; 13 H14 0x449B30 jmp (unit order, vt+0x7C)
    jmp near acc80          ; 14 H15 0x449CD0 jmp (unit order, vt+0x80)

; ---------------------------------------------------------------- helpers
; eax = &rand state of this thread; clobbers ecx, edx
randptr:
    call 0x4BD603
    add eax, 0x14
    ret

; ebx = player object -> eax = index or 0xFF; clobbers edx, ecx
player_index:
    mov edx, [0x508300]
    xor eax, eax
    test edx, edx
    jz .none
    mov ecx, [edx + 5]
    cmp ecx, 8
    jbe .loop
    mov ecx, 8
.loop:
    cmp eax, ecx
    jae .none
    cmp [edx + 9 + eax*4], ebx
    je .found
    inc eax
    jmp .loop
.none:
    mov eax, 0xFF
.found:
    ret

; eax = hash of every player's adamas/platinum (simulation state check); keeps other regs
state_hash:
    push ecx
    push edx
    push esi
    push edi
    xor eax, eax
    mov esi, [0x508300]
    test esi, esi
    jz .done
    mov ecx, [esi + 5]
    cmp ecx, 8
    jbe .ok
    mov ecx, 8
.ok:
    xor edx, edx
.loop:
    cmp edx, ecx
    jae .done
    mov edi, [esi + 9 + edx*4]
    test edi, edi
    jz .next
    imul eax, eax, 31
    add eax, [edi + 0x1A]
    imul eax, eax, 31
    add eax, [edi + 0x1E]
.next:
    inc edx
    jmp .loop
.done:
    pop edi
    pop esi
    pop edx
    pop ecx
    ret

; write BUF[0..V_BUFLEN) to the record file
flush:
    mov ecx, [V_BUFLEN]
    test ecx, ecx
    jz .done
    cmp dword [C_REC], 1
    jne .reset
    push 0
    push V_TMPW
    push ecx
    push BUF
    push dword [V_HANDLE]
    call WriteFile
.reset:
    mov dword [V_BUFLEN], 0
.done:
    ret

; esi = source, ecx = length (< BUF_SIZE). Caller holds GAME_CS.
buf_put:
    mov eax, [V_BUFLEN]
    add eax, ecx
    cmp eax, BUF_SIZE
    jbe .copy
    push esi
    push ecx
    call flush
    pop ecx
    pop esi
.copy:
    mov edi, [V_BUFLEN]
    add [V_BUFLEN], ecx
    add edi, BUF
    rep movsb
    ret

; ---------------------------------------------------------------- record
rec_close:
    cmp dword [C_REC], 1
    jne .done
    mov byte [V_TMP], R_END
    mov eax, [0x6C946C]
    mov [V_TMP + 1], eax
    mov esi, V_TMP
    mov ecx, 5
    call buf_put
    call flush
    push dword [V_HANDLE]
    call CloseHandle
    mov dword [C_REC], 0
.done:
    ret

rec_open:
    push GAME_CS
    call EnterCS
    call rec_close
    ; "Replays\_rec_XXXXXXXX.iopraw"
    mov esi, rec_prefix
    mov edi, V_NAME
    mov ecx, rec_prefix_len
    rep movsb
    call GetTickCount
    mov edx, eax
    mov ecx, 8
.hex:
    rol edx, 4
    mov eax, edx
    and eax, 15
    mov al, [hexdigits + eax]
    stosb
    loop .hex
    mov esi, rec_suffix
    mov ecx, rec_suffix_len
    rep movsb
    push 0
    push 0x80               ; FILE_ATTRIBUTE_NORMAL
    push 2                  ; CREATE_ALWAYS
    push 0
    push 1                  ; FILE_SHARE_READ
    push 0x40000000         ; GENERIC_WRITE
    push V_NAME
    call CreateFileA
    cmp eax, -1
    je .fail
    mov [V_HANDLE], eax
    mov dword [C_REC], 1
    mov dword [C_CMDS], 0
    mov dword [V_TSF], 0
    ; header
    mov edi, BUF
    xor eax, eax
    mov ecx, HDR_SIZE / 4
    rep stosd
    mov dword [BUF + H_MAGIC], 'IOPR'
    mov dword [BUF + H_VERSION], 1
    movzx eax, byte [0x4E53CC]
    mov [BUF + H_MODE], eax
    mov eax, [V_RANDPRE]
    mov [BUF + H_RANDPRE], eax
    call randptr
    mov eax, [eax]
    mov [BUF + H_RANDPOST], eax
    mov eax, [0x4E4678]
    mov [BUF + H_LOCALID], eax
    mov eax, [0x4DE0F8]
    mov [BUF + H_TPS], eax
    mov eax, [0x508300]
    movzx eax, byte [eax]
    mov [BUF + H_LOCALIDX], eax
    mov eax, [0x6C946C]
    mov [BUF + H_STICK], eax
    mov esi, BLOCK
    mov edi, BUF + H_BLOCK
    mov ecx, BLOCK_LEN
    rep movsb
    mov dword [V_BUFLEN], HDR_SIZE
    call flush
.fail:
    push GAME_CS
    call LeaveCS
    ret

; H1/H2: keep the rand state the builder starts from
prebuild1:
    pushad
    call randptr
    mov eax, [eax]
    mov [V_RANDPRE], eax
    popad
    jmp 0x4055E0

prebuild2:
    pushad
    call randptr
    mov eax, [eax]
    mov [V_RANDPRE], eax
    popad
    jmp 0x48F0A0

; H3 (ecx = root): skirmish setup. Playback rebuilds the recorded match instead.
setup1:
    cmp dword [C_MODE], 1
    je .play
    jmp 0x4058B0
.play:
    pushad
    mov esi, [V_PBASE]
    lea esi, [esi + H_BLOCK]
    mov edi, BLOCK
    mov ecx, BLOCK_LEN
    rep movsb
    mov esi, [V_PBASE]
    mov eax, [esi + H_LOCALID]
    mov [0x4E4678], eax
    call randptr
    mov ecx, [esi + H_RANDPRE]
    mov [eax], ecx
    cmp dword [esi + H_MODE], 2
    je .net
    call 0x4055E0
    jmp .done
.net:
    call 0x48F0A0
.done:
    popad
    ret

; H4 (ecx = root): every skirmish/network setup converges here before the map loads
postsetup:
    pushad
    cmp dword [C_MODE], 1
    je .play
    cmp dword [C_NOREC], 0
    jne .out
    movzx eax, byte [0x4E53CC]
    cmp eax, 1
    jb .out
    cmp eax, 2
    ja .out
    call rec_open
    jmp .out
.play:
    mov esi, [V_PBASE]
    call randptr
    mov ecx, [esi + H_RANDPOST]
    mov [eax], ecx
    push dword [esi + H_TPS]
    call 0x4B56B0
    add esp, 4
    mov dword [C_STARTED], 1
.out:
    popad
    jmp 0x405CE0

; H6 (ecx = team manager): one simulation tick's queued commands
tickexec:
    cmp dword [C_MODE], 1
    je play_tick
    cmp dword [C_REC], 1
    je .rec
    jmp 0x435B20
.rec:
    pushad
    push GAME_CS
    call EnterCS
    mov byte [V_TMP], R_TICK
    mov eax, [0x6C946C]
    mov [V_TMP + 1], eax
    mov [C_TICK], eax
    call randptr
    mov eax, [eax]
    mov [V_TMP + 5], eax
    call state_hash
    mov [V_TMP + 9], eax
    mov esi, V_TMP
    mov ecx, 13
    call buf_put
    inc dword [V_TSF]
    cmp dword [V_TSF], 256
    jb .noflush
    mov dword [V_TSF], 0
    call flush
.noflush:
    push GAME_CS
    call LeaveCS
    popad
    mov dword [V_INEXEC], 1
    call 0x435B20
    mov dword [V_INEXEC], 0
    ret

; H7 (ecx = player object, [esp+4] = packet): every executed command, in order
cmdexec:
    cmp dword [C_REC], 1
    jne .go
    pushad
    mov ebx, ecx
    call player_index
    mov byte [V_TMP], R_CMD
    mov [V_TMP + 1], al
    mov esi, [esp + 36]
    mov edi, V_TMP + 2
    mov ecx, 0x6F
    rep movsb
    mov esi, V_TMP
    mov ecx, 0x71
    call buf_put
    inc dword [C_CMDS]
    popad
.go:
    jmp 0x435C50

; H8 (jmp from 0x434060, ecx = player, [esp+4] a1, [esp+8] a2): a player leaves
leave:
    cmp dword [C_REC], 1
    jne .orig
    cmp dword [V_INEXEC], 0
    jne .orig
    pushad
    mov ebx, ecx
    call player_index
    mov ebx, eax
    push GAME_CS
    call EnterCS
    mov byte [V_TMP], R_LEAVE
    mov [V_TMP + 1], bl
    mov eax, [esp + 36]
    mov [V_TMP + 2], eax
    mov eax, [esp + 40]
    mov [V_TMP + 6], eax
    mov eax, [0x6C946C]
    mov [V_TMP + 10], eax
    mov esi, V_TMP
    mov ecx, 14
    call buf_put
    push GAME_CS
    call LeaveCS
    popad
.orig:
    sub esp, 0x0C
    push ebx
    push ebp
    jmp 0x434065

; H10 (ecx = root): the battle is over
battleend:
    pushad
    push GAME_CS
    call EnterCS
    call rec_close
    push GAME_CS
    call LeaveCS
    popad
    jmp 0x405F30

; ---------------------------------------------------------------- playback
; H9 (esi = root, edi = 3): replaces "mov [esi+0x41CD], edi" before the menu loop
menu:
    cmp dword [C_MODE], 1
    je .play
    mov [esi + 0x41CD], edi
    ret
.play:
    cmp dword [C_STARTED], 0
    je .start
    push 0
    call ExitProcess
.start:
    pushad
    call play_load
    popad
    mov byte [0x4E53CC], 1
    mov dword [esi + 0x41CD], 2
    mov byte [esi + 0x41D1], 0
    mov dword [0x507AAC], 0
    mov ecx, [esi + 0xC2C]
    test ecx, ecx
    jz .nocd
    pushad
    call 0x48C2D0
    popad
.nocd:
    mov dword [esp], 0x403F96          ; skip the menu loop
    ret

play_fail:
    push 0x10                          ; MB_ICONERROR
    push msg_title
    push msg_open
    push 0
    call MessageBoxA
    push 1
    call ExitProcess

play_load:
    push 0
    push 0x80
    push 3                  ; OPEN_EXISTING
    push 0
    push 1
    push 0x80000000         ; GENERIC_READ
    push play_name
    call CreateFileA
    cmp eax, -1
    je play_fail
    mov ebx, eax
    push 0
    push ebx
    call GetFileSize
    cmp eax, HDR_SIZE
    jb play_fail
    mov ebp, eax
    lea eax, [ebp + 16]
    push eax
    call 0x4B3180            ; game malloc
    add esp, 4
    test eax, eax
    jz play_fail
    mov edi, eax
    push 0
    push V_TMPW
    push ebp
    push edi
    push ebx
    call ReadFile
    push ebx
    call CloseHandle
    cmp dword [edi + H_MAGIC], 'IOPR'
    jne play_fail
    cmp dword [edi + H_VERSION], 1
    jne play_fail
    mov [V_PBASE], edi
    lea eax, [edi + HDR_SIZE]
    mov [V_PPTR], eax
    lea eax, [edi + ebp]
    mov [V_PEND], eax
    ; end tick = last complete TICK record
    mov esi, [V_PPTR]
    mov edx, [edi + H_STICK]
.scan:
    cmp esi, [V_PEND]
    jae .scanned
    movzx eax, byte [esi]
    cmp al, R_TICK
    jne .s2
    lea ecx, [esi + 13]
    cmp ecx, [V_PEND]
    ja .scanned
    mov edx, [esi + 1]
    mov esi, ecx
    jmp .scan
.s2:
    cmp al, R_CMD
    jne .s3
    add esi, 0x71
    jmp .scan
.s3:
    cmp al, R_LEAVE
    jne .scanned
    add esi, 14
    jmp .scan
.scanned:
    mov [C_ENDTICK], edx
    ret

; H6 in playback (ecx = team manager): feed the recorded commands of this tick
play_tick:
    pushad
    mov ebp, ecx
    push GAME_CS
    call EnterCS
    mov ebx, [0x6C946C]
    mov [C_TICK], ebx
    mov esi, [V_PPTR]
.loop:
    cmp esi, [V_PEND]
    jae .eof
    movzx eax, byte [esi]
    cmp al, R_TICK
    jne .notick
    lea edx, [esi + 13]
    cmp edx, [V_PEND]
    ja .eof
    cmp [esi + 1], ebx
    ja .done                           ; belongs to a later tick
    call state_hash
    cmp eax, [esi + 9]
    je .hashok
    inc dword [C_DESYNC]               ; resources differ from the recording
    cmp dword [C_FIRSTBAD], 0
    jne .hashok
    lea edx, [ebx + 1]
    mov [C_FIRSTBAD], edx
.hashok:
    call randptr
    mov ecx, [esi + 5]
    cmp [eax], ecx
    je .randok
    inc dword [C_RANDFIX]
    mov [eax], ecx                     ; keep the recorded random sequence
.randok:
    add esi, 13
    jmp .loop
.notick:
    cmp al, R_CMD
    jne .noleave0
    lea edx, [esi + 0x71]
    cmp edx, [V_PEND]
    ja .eof
    movzx eax, byte [esi + 1]
    cmp eax, [ebp + 5]
    jae .skipcmd
    mov ecx, [ebp + 9 + eax*4]
    lea edx, [esi + 2]
    push edx
    call 0x435C50
.skipcmd:
    add esi, 0x71
    jmp .loop
.noleave0:
    cmp al, R_LEAVE
    jne .eof
    lea edx, [esi + 14]
    cmp edx, [V_PEND]
    ja .eof
    movzx eax, byte [esi + 1]
    cmp eax, [ebp + 5]
    jae .skipleave
    mov ecx, [ebp + 9 + eax*4]
    push dword [esi + 6]
    push dword [esi + 2]
    call 0x434060
.skipleave:
    add esi, 14
    jmp .loop
.eof:
    mov dword [C_ENDED], 1
.done:
    mov [V_PPTR], esi
    push GAME_CS
    call LeaveCS
    popad
    ret

; H5: time-based tick advance. Playback adds speed, pause, single step and the end hold.
tickadv:
    cmp dword [C_MODE], 1
    je .play
    jmp 0x48E990
.play:
    pushad
    mov eax, [C_SPEED]
    test eax, eax
    jz .speedok
    cmp eax, [0x4DE0F8]
    je .speedok
    push eax
    call 0x4B56B0
    add esp, 4
.speedok:
    mov eax, [C_VISION]
    cmp eax, [V_LASTVIS]
    je .visok
    mov [V_LASTVIS], eax
    xor ecx, ecx
    cmp eax, 1
    setne cl
    mov [0x4DC668], ecx                ; 0 = no fog of war at all (display flag)
    mov ecx, [0x508188]
    test ecx, ecx
    jz .visok
    call 0x42DA60                      ; recompute fog for the new viewpoint (display only)
.visok:
    cmp dword [C_ENDED], 0
    jne .hold
    cmp dword [C_PAUSED], 0
    je .run
    cmp dword [C_STEP], 0
    je .hold
    dec dword [C_STEP]
    inc dword [0x6C946C]
    call TimeGetTime
    mov [0x6C9464], eax
    popad
    ret
.hold:
    call TimeGetTime
    mov [0x6C9464], eax
    push 1
    call Sleep
    popad
    ret
.run:
    popad
    jmp 0x48E990

; H11 (fog loop: eax = viewer index, edx = owner object): ZF=1 reveals the owner's units
vision:
    cmp dword [C_MODE], 1
    jne .orig
    cmp dword [C_VISION], 0
    je .orig
    cmp dword [C_VISION], 1
    jne .player
    cmp eax, eax
    ret
.player:
    push eax
    mov eax, [C_VISION]
    sub eax, 2
    cmp byte [eax + edx + 0x11], 1
    pop eax
    ret
.orig:
    cmp byte [eax + edx + 0x11], 1
    ret

;
; H12 0x4247D0(owner): "player is controlled here". Playback answers yes for everyone so any
; unit or building can be selected and its production/upgrade progress shows in the panel.
; Only UI code calls it (selection 0x4A1F30/0x4838B0, panel 0x4A25C0); commands are ignored.
control:
    cmp dword [C_MODE], 1
    jne .orig
    mov eax, 1
    ret
.orig:
    push esi
    mov esi, [0x508300]
    jmp 0x4247D7

; ---------------------------------------------------------------- rally -> harvest
; Unit orders reach the mobile-unit order component through vt+0x78/0x7C/0x80 (this = component,
; [esp+4] = 0x32-byte order record: +5 command, +0x12 target id, +0x1A, +0x2A x, +0x2E y).
; A production building sends its new unit a move to the rally point (0xBB8/0xBB9, no target).
; When the unit is a worker (Harvester 0x05000001, Preverian 0x13400011, Sinjong 0x05200024)
; and the point lies on a refinery, the game's own right-click chooser (vt+0x6C, 0x45A700) is
; asked about that refinery; if it answers harvest (0x7D3) the record becomes that order.
; Sim-side and deterministic (object table order), so every PC must run this patch.
acc78:
    call rally_fix
    push ebx
    push ebp
    mov ebp, ecx
    mov eax, [ebp - 0x4F3]
    jmp 0x4495CA
acc7c:
    call rally_fix
    push ebx
    mov ebx, ecx
    mov eax, [ebx - 0x4F3]
    jmp 0x449B39
acc80:
    call rally_fix
    push ebx
    mov ebx, ecx
    mov eax, [ebx - 0x4F3]
    jmp 0x449CD9

; eax = object (table pointer) -> eax = type id, ecx = x, edx = y (pixels)
obj_info:
    push esi
    mov esi, eax
    mov edx, [esi + 4]                 ; virtual base table
    mov ecx, [edx + 4]
    mov eax, [esi + ecx + 8]
    mov eax, [eax + 4]
    mov edx, [edx + 8]
    mov ecx, [esi + edx + 0x1D]
    mov edx, [esi + edx + 0x21]
    pop esi
    ret

rally_fix:                             ; [esp+4] caller return, [esp+8] record
    pushad
    mov esi, [esp + 40]
    test esi, esi
    jz .out
    mov eax, [esi + 5]
    cmp eax, 0xBB8
    je .move
    cmp eax, 0xBB9
    jne .out
.move:
    cmp dword [esi + 0x12], 0
    jne .out
    lea edi, [ecx - 0x4F7]             ; the unit as stored in the object table
    mov eax, edi
    call obj_info
    cmp eax, 0x05000001
    je .worker
    cmp eax, 0x13400011
    je .worker
    cmp eax, 0x05200024
    jne .out
.worker:
    mov eax, [esi + 0x2A]
    mov [V_RX], eax
    mov eax, [esi + 0x2E]
    mov [V_RY], eax
    mov dword [V_BEST], 0
    mov dword [V_BESTD], 96 * 96 + 1
    mov eax, [0x507FA8]
    test eax, eax
    jz .out
    mov ebx, [eax]                     ; objects 0..2999
    test ebx, ebx
    jz .out
    xor ebp, ebp
.scan:
    cmp ebp, 0xBB8
    jae .scanned
    mov eax, [ebx + ebp*4]
    test eax, eax
    jz .next
    push eax
    call obj_info
    mov ecx, eax
    and ecx, 0xFFFFFFF0
    cmp ecx, 0x18000000
    jne .notref
    and eax, 15
    mov ecx, 0x3D4                     ; refineries 0x18000002/4/6/7/8/9
    bt ecx, eax
    jnc .notref
    pop eax
    jmp .isref
.notref:
    pop eax
    jmp .next
.isref:
    push eax
    call obj_info
    pop eax
    sub ecx, [V_RX]
    sub edx, [V_RY]
    cmp ecx, -96
    jl .next
    cmp ecx, 96
    jg .next
    cmp edx, -96
    jl .next
    cmp edx, 96
    jg .next
    imul ecx, ecx
    imul edx, edx
    add ecx, edx
    cmp ecx, [V_BESTD]
    jae .next
    mov [V_BESTD], ecx
    mov [V_BEST], eax
.next:
    inc ebp
    jmp .scan
.scanned:
    mov eax, [V_BEST]
    test eax, eax
    jz .out
    push eax                           ; chooser(target)
    mov eax, [edi]
    mov ecx, edi
    call [eax + 0x6C]
    cmp eax, 0x7D3
    jne .out
    mov eax, [V_BEST]
    mov edx, [eax + 4]
    mov edx, [edx + 4]
    lea ecx, [eax + edx + 4]
    call 0x46D320                      ; object id
    mov dword [esi + 5], 0x7D3
    mov [esi + 0x12], eax
    mov dword [esi + 0x1A], 0
.out:
    popad
    ret

; ---------------------------------------------------------------- data
hexdigits:   db "0123456789ABCDEF"
rec_prefix:  db "Replays\_rec_"
rec_prefix_len equ $ - rec_prefix
rec_suffix:  db ".iopraw", 0
rec_suffix_len equ $ - rec_suffix
play_name:   db "Replays\_play.iopraw", 0
msg_title:   db "IOP Replay", 0
msg_open:    db "Replays\_play.iopraw", 13, 10, "replay file missing or invalid.", 0

code_end:
    times (0x1000 - ($ - $$)) db 0
; ---- CTRL page (initialised part)
ctrl_magic:  dd 'IOPC'
ctrl_ver:    dd 1
ctrl_mode:   dd 0
    times (0x1000 - 12) db 0
