"""Board display mentions with the same token boundaries as node ``subs::mentions``.

A mention retains untrusted message data in a filtered view; it grants no authority.
"""


_NAME = frozenset(b'abcdefghijklmnopqrstuvwxyz0123456789._-')
_ALNUM = frozenset(b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')

def mentions(text: str, name: str) -> bool:
    raw, wanted = text.encode('utf-8'), name.encode('utf-8')
    offset = 0
    while (start := raw.find(b'@', offset)) >= 0:
        before = start == 0 or not (raw[start - 1] in _ALNUM or raw[start - 1] in b'_/@')
        end = start + 1
        while end < len(raw) and raw[end] in _NAME:
            end += 1
        found = raw[start + 1:end].rstrip(b'.')
        end = start + 1 + len(found)
        if before and found == wanted and (end == len(raw) or raw[end] == 46 or not (raw[end] in _NAME or raw[end] == 47)):
            return True
        offset = start + 1
    return False
