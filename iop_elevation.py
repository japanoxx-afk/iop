"""Waitable Windows elevation; used from a launcher worker thread."""
import ctypes
from ctypes import wintypes as w
import subprocess

def elevated_wait(executable, args, directory):
    class Info(ctypes.Structure):
        _fields_=[('cbSize',w.DWORD),('fMask',w.ULONG),('hwnd',w.HWND),('lpVerb',w.LPCWSTR),('lpFile',w.LPCWSTR),('lpParameters',w.LPCWSTR),('lpDirectory',w.LPCWSTR),('nShow',ctypes.c_int),('hInstApp',w.HINSTANCE),('lpIDList',w.LPVOID),('lpClass',w.LPCWSTR),('hkeyClass',w.HKEY),('dwHotKey',w.DWORD),('hIcon',w.HANDLE),('hProcess',w.HANDLE)]
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    shell.ShellExecuteExW.argtypes=[ctypes.POINTER(Info)]; shell.ShellExecuteExW.restype=w.BOOL
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]; kernel.WaitForSingleObject.restype=w.DWORD
    kernel.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]; kernel.GetExitCodeProcess.restype=w.BOOL
    kernel.CloseHandle.argtypes=[w.HANDLE]
    info=Info(); info.cbSize=ctypes.sizeof(info); info.fMask=0x40|0x100
    info.lpVerb='runas'; info.lpFile=executable; info.lpParameters=subprocess.list2cmdline(args); info.lpDirectory=str(directory); info.nShow=0
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        raise OSError('관리자 작업이 취소되었거나 실행되지 않았습니다.')
    try:
        if kernel.WaitForSingleObject(info.hProcess,0xffffffff)!=0: raise OSError('관리자 작업 대기 실패')
        code=w.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess,ctypes.byref(code)) or code.value:
            raise OSError('hosts 등록에 실패했습니다. 관리자 권한과 보안 프로그램 설정을 확인하세요.')
    finally: kernel.CloseHandle(info.hProcess)
