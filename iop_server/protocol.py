"""IOP Big Brain wire format recovered from the supplied x86 client."""
import asyncio
import struct

HEADER = struct.Struct('<BHH')
MAX_FRAME = 1029  # client's MSG_PEEK buffer is 0x405 bytes
REQUEST_SIZES = {1:20, 3:42, 5:42, 7:73, 9:10, 10:52, 12:0, 13:13, 14:0, 16:0}

def packet(kind, value=0, payload=b''):
    size = HEADER.size + len(payload)
    if not HEADER.size <= size <= MAX_FRAME:
        raise ValueError('invalid frame size')
    return HEADER.pack(kind, value, size) + payload

async def read_packet(reader):
    head = await reader.readexactly(5)
    kind, value, size = HEADER.unpack(head)
    if not 5 <= size <= MAX_FRAME:
        raise ValueError(f'invalid frame length: {size}')
    body = await reader.readexactly(size - 5)
    if kind in REQUEST_SIZES and len(body) != REQUEST_SIZES[kind]:
        raise ValueError(f'invalid request {kind} size: {len(body)}')
    return kind, value, body

def cstring(raw):
    return raw.split(b'\0', 1)[0]

def fixed(raw, size):
    raw = cstring(raw)
    if len(raw) >= size:
        raise ValueError('string too long')
    return raw.ljust(size, b'\0')

def list_packets(kind, records):
    if not records:
        return [packet(kind)]
    count = (MAX_FRAME - 5) // len(records[0])
    return [packet(kind, len(records[i:i+count]), b''.join(records[i:i+count]))
            for i in range(0, len(records), count)]
