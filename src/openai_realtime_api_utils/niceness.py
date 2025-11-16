# Drafted by GPT-5  
# Cross-platform helpers to set process niceness and per-thread priority.  
# Linux: uses libc.setpriority(PRIO_PROCESS, tid, nice) for the *current thread*  
# Windows: uses SetThreadPriority for the *current thread*.  

import sys
import ctypes as ct
import threading
from enum import Enum

class ThreadPriority(Enum):
    low = 'low'
    belowNormal = 'below_normal'
    normal = 'normal'
    aboveNormal = 'above_normal'
    high = 'high'
    realtime = 'realtime'  # requires elevated rights; use with care


def set_current_thread_priority(level: ThreadPriority) -> None:
    '''
    Set priority of the *current Python thread* (not all threads).
    Windows: maps to SetThreadPriority.
    Linux: adjusts the thread's 'nice' via setpriority for the calling TID.
           (No root required for >= current nice; lowering niceness usually needs CAP_SYS_NICE.)
    '''
    if sys.platform.startswith('win'):
        _setCurrentThreadPriorityWindows(level)
    else:
        _linux_set_current_thread_nice(_linux_map_priority_to_nice_delta(level))


class NicenessManager:
    def __init__(self) -> None:
        self.has_set = False
    
    def maybe_set(self, level: ThreadPriority) -> None:
        if not self.has_set:
            set_current_thread_priority(level)
            self.has_set = True


def _linux_set_current_thread_nice(delta: int) -> None:
    '''
    Adjust niceness of the *current* thread by a delta using setpriority(PRIO_PROCESS, tid, new_nice).
    On Linux, threading.get_native_id() returns the kernel TID for the calling thread.
    '''
    libc = ct.CDLL('libc.so.6', use_errno=True)
    setpriority = libc.setpriority
    setpriority.argtypes = (ct.c_int, ct.c_int, ct.c_int)
    setpriority.restype = ct.c_int

    # Constants from <sys/resource.h>
    PRIO_PROCESS = 0
    # Read current nice via getpriority if available; otherwise assume 0 baseline
    try:
        getpriority = libc.getpriority
        getpriority.argtypes = (ct.c_int, ct.c_int)
        getpriority.restype = ct.c_int
        # errno handling quirk: getpriority can return -1 legitimately; clear errno first
        ct.set_errno(0)
        current = getpriority(PRIO_PROCESS, _linux_thread_id())
        err = ct.get_errno()
        if err != 0:
            current = 0
    except Exception:
        current = 0

    new_nice = int(max(-20, min(19, current + delta)))
    rc = setpriority(PRIO_PROCESS, _linux_thread_id(), new_nice)
    if rc != 0:
        err = ct.get_errno()
        raise OSError(err, f'setpriority failed for TID={_linux_thread_id()} new_nice={new_nice}')


def _linux_thread_id() -> int:
    '''
    Return the calling thread's TID (Lightweight Process ID).
    Python 3.8+: threading.get_native_id() is the TID on Linux.
    '''
    return int(threading.get_native_id())


def _linux_map_priority_to_nice_delta(level: ThreadPriority) -> int:
    '''
    Heuristic mapping: thread-priority "levels" → niceness delta.
    Positive delta = lower priority; negative delta = higher priority.
    '''
    table = {
        ThreadPriority.low: +10,
        ThreadPriority.belowNormal: +5,
        ThreadPriority.normal: 0,
        ThreadPriority.aboveNormal: -5,
        ThreadPriority.high: -10,
        ThreadPriority.realtime: -15,  # likely requires CAP_SYS_NICE/root
    }
    if level not in table:
        raise ValueError(f'unknown priority level: {level}')
    return table[level]


def _setCurrentThreadPriorityWindows(level: ThreadPriority) -> None:
    mapping = {
        ThreadPriority.low: -2,           # THREAD_PRIORITY_LOWEST
        ThreadPriority.belowNormal: -1,   # THREAD_PRIORITY_BELOW_NORMAL
        ThreadPriority.normal: 0,         # THREAD_PRIORITY_NORMAL
        ThreadPriority.aboveNormal: +1,   # THREAD_PRIORITY_ABOVE_NORMAL
        ThreadPriority.high: +2,          # THREAD_PRIORITY_HIGHEST
        ThreadPriority.realtime: 15,      # THREAD_PRIORITY_TIME_CRITICAL (dangerous)
    }
    if level not in mapping:
        raise ValueError(f'unknown priority level: {level}')

    value = mapping[level]

    k32 = ct.WinDLL('kernel32', use_last_error=True)    # type: ignore
    GetCurrentThread = k32.GetCurrentThread
    SetThreadPriority = k32.SetThreadPriority
    SetThreadPriority.argtypes = (ct.c_void_p, ct.c_int)
    SetThreadPriority.restype = ct.c_int

    hthread = GetCurrentThread()
    ok = SetThreadPriority(hthread, value)
    if not ok:
        raise OSError(ct.get_last_error(), 'SetThreadPriority failed')  # type: ignore
