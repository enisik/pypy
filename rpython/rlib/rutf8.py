""" This file is about supporting unicode strings in RPython,
represented by a byte string that is exactly the UTF-8 version
(for some definition of UTF-8).

This doesn't support Python 2's unicode characters beyond 0x10ffff,
which are theoretically possible to obtain using strange tricks like
the array or ctypes modules.

Fun comes from surrogates.  Various functions don't normally accept
any unicode character betwen 0xd800 and 0xdfff, but do if you give
the 'allow_surrogates = True' flag.

This is a minimal reference implementation.  A lot of interpreters
need their own copy-pasted copy of some of the logic here, with
extra code in the middle for error handlers and so on.
"""

import sys
from rpython.rlib.objectmodel import enforceargs, we_are_translated, specialize
from rpython.rlib.rstring import StringBuilder
from rpython.rlib import jit
from rpython.rlib.signature import signature
from rpython.rlib.types import char, none
from rpython.rlib.rarithmetic import r_uint
from rpython.rlib.unicodedata import unicodedb
from rpython.rtyper.lltypesystem import lltype, rffi


def unichr_as_utf8(code, allow_surrogates=False):
    """Encode code (numeric value) as utf8 encoded string
    """
    assert code >= 0
    code = r_uint(code)
    if code <= r_uint(0x7F):
        # Encode ASCII
        return chr(code)
    if code <= r_uint(0x07FF):
        return chr((0xc0 | (code >> 6))) + chr((0x80 | (code & 0x3f)))
    if code <= r_uint(0xFFFF):
        if not allow_surrogates and 0xD800 <= code <= 0xDfff:
            raise ValueError
        return (chr((0xe0 | (code >> 12))) +
                chr((0x80 | ((code >> 6) & 0x3f))) +
                chr((0x80 | (code & 0x3f))))
    if code <= r_uint(0x10FFFF):
        return (chr((0xf0 | (code >> 18))) +
                chr((0x80 | ((code >> 12) & 0x3f))) +
                chr((0x80 | ((code >> 6) & 0x3f))) +
                chr((0x80 | (code & 0x3f))))
    raise ValueError

def unichr_as_utf8_append(builder, code, allow_surrogates=False):
    """Encode code (numeric value) as utf8 encoded string
    and emit the result into the given StringBuilder.
    Raises ValueError if the code is outside range(0x110000).
    """
    code = r_uint(code)
    if code <= r_uint(0x7F):
        # Encode ASCII
        builder.append(chr(code))
        return
    if code <= r_uint(0x07FF):
        builder.append(chr((0xc0 | (code >> 6))))
        builder.append(chr((0x80 | (code & 0x3f))))
        return
    if code <= r_uint(0xFFFF):
        if not allow_surrogates and 0xd800 <= code <= 0xdfff:
            raise ValueError
        builder.append(chr((0xe0 | (code >> 12))))
        builder.append(chr((0x80 | ((code >> 6) & 0x3f))))
        builder.append(chr((0x80 | (code & 0x3f))))
        return
    if code <= r_uint(0x10FFFF):
        builder.append(chr((0xf0 | (code >> 18))))
        builder.append(chr((0x80 | ((code >> 12) & 0x3f))))
        builder.append(chr((0x80 | ((code >> 6) & 0x3f))))
        builder.append(chr((0x80 | (code & 0x3f))))
        return
    raise ValueError

# note - table lookups are really slow. Measured on various elements of obama
#        chinese wikipedia, they're anywhere between 10% and 30% slower.
#        In extreme cases (small, only chinese text), they're 40% slower

def next_codepoint_pos(code, pos):
    """Gives the position of the next codepoint after pos.
    Assumes valid utf8.  'pos' must be before the end of the string.
    """
    assert pos >= 0
    chr1 = ord(code[pos])
    if chr1 <= 0x7F:
        return pos + 1
    if chr1 <= 0xDF:
        return pos + 2
    if chr1 <= 0xEF:
        return pos + 3
    return pos + 4

def prev_codepoint_pos(code, pos):
    """Gives the position of the previous codepoint.
    'pos' must not be zero.
    """
    pos -= 1 # ruint
    if pos >= len(code):     # for the case where pos - 1 == len(code):
        assert pos >= 0
        return pos           # assume there is an extra '\x00' character
    chr1 = ord(code[pos])
    if chr1 <= 0x7F:
        assert pos >= 0
        return pos
    pos -= 1
    if ord(code[pos]) >= 0xC0:
        assert pos >= 0
        return pos
    pos -= 1
    if ord(code[pos]) >= 0xC0:
        assert pos >= 0
        return pos
    pos -= 1
    assert pos >= 0
    return pos

def compute_length_utf8(s):
    continuation_bytes = 0
    for i in range(len(s)):
        if 0x80 <= ord(s[i]) <= 0xBF:    # count the continuation bytes
            continuation_bytes += 1
    return len(s) - continuation_bytes

def codepoint_at_pos(code, pos):
    """ Give a codepoint in code at pos - assumes valid utf8, no checking!
    """
    ordch1 = ord(code[pos])
    if ordch1 <= 0x7F:
        return ordch1

    ordch2 = ord(code[pos+1])
    if ordch1 <= 0xDF:
        # 110yyyyy 10zzzzzz -> 00000000 00000yyy yyzzzzzz
        return (((ordch1 & 0x1F) << 6) +    # 0b00011111
                 (ordch2 & 0x3F))           # 0b00111111

    ordch3 = ord(code[pos+2])
    if ordch1 <= 0xEF:
        # 1110xxxx 10yyyyyy 10zzzzzz -> 00000000 xxxxyyyy yyzzzzzz
        return (((ordch1 & 0x0F) << 12) +     # 0b00001111
                ((ordch2 & 0x3F) << 6) +      # 0b00111111
                (ordch3 & 0x3F))              # 0b00111111

    ordch4 = ord(code[pos+3])
    if True:
        # 11110www 10xxxxxx 10yyyyyy 10zzzzzz -> 000wwwxx xxxxyyyy yyzzzzzz
        return (((ordch1 & 0x07) << 18) +      # 0b00000111
                ((ordch2 & 0x3F) << 12) +      # 0b00111111
                ((ordch3 & 0x3F) << 6) +       # 0b00111111
                (ordch4 & 0x3F))               # 0b00111111
    assert False, "unreachable"

def codepoint_before_pos(code, pos):
    """Give a codepoint in code at the position immediately before pos
    - assumes valid utf8, no checking!
    """
    pos = r_uint(pos)
    ordch1 = ord(code[pos-1])
    if ordch1 <= 0x7F:
        return ordch1

    ordch2 = ordch1
    ordch1 = ord(code[pos-2])
    if ordch1 >= 0xC0:
        # 110yyyyy 10zzzzzz -> 00000000 00000yyy yyzzzzzz
        return (((ordch1 & 0x1F) << 6) +    # 0b00011111
                 (ordch2 & 0x3F))           # 0b00111111

    ordch3 = ordch2
    ordch2 = ordch1
    ordch1 = ord(code[pos-3])
    if ordch1 >= 0xC0:
        # 1110xxxx 10yyyyyy 10zzzzzz -> 00000000 xxxxyyyy yyzzzzzz
        return (((ordch1 & 0x0F) << 12) +     # 0b00001111
                ((ordch2 & 0x3F) << 6) +      # 0b00111111
                (ordch3 & 0x3F))              # 0b00111111

    ordch4 = ordch3
    ordch3 = ordch2
    ordch2 = ordch1
    ordch1 = ord(code[pos-4])
    if True:
        # 11110www 10xxxxxx 10yyyyyy 10zzzzzz -> 000wwwxx xxxxyyyy yyzzzzzz
        return (((ordch1 & 0x07) << 18) +      # 0b00000111
                ((ordch2 & 0x3F) << 12) +      # 0b00111111
                ((ordch3 & 0x3F) << 6) +       # 0b00111111
                (ordch4 & 0x3F))               # 0b00111111
    assert False, "unreachable"

class CheckError(Exception):
    def __init__(self, pos):
        self.pos = pos

def check_ascii(s):
    res = first_non_ascii_char(s)
    if res < 0:
        return
    raise CheckError(res)

@jit.elidable
def first_non_ascii_char(s):
    for i in range(len(s)):
        if ord(s[i]) > 0x7F:
            return i
    return -1

def islinebreak(s, pos):
    chr1 = ord(s[pos])
    if 0xa <= chr1 <= 0xd:
        return True
    if 0x1c <= chr1 <= 0x1e:
        return True
    if chr1 == 0xc2:
        chr2 = ord(s[pos + 1])
        return chr2 == 0x85
    elif chr1 == 0xe2:
        chr2 = ord(s[pos + 1])
        if chr2 != 0x80:
            return False
        chr3 = ord(s[pos + 2])
        return chr3 == 0xa8 or chr3 == 0xa9
    return False

def isspace(s, pos):
    chr1 = ord(s[pos])
    if (chr1 == ord(' ') or chr1 == ord('\n') or chr1 == ord('\t') or
        chr1 == ord('\r')):
        return True # common
    if chr1 == 0x0b or chr1 == 0x0c or (chr1 >= 0x1c and chr1 <= 0x1f):
        return True # less common
    if chr1 < 0x80:
        return False
    # obscure cases
    chr2 = ord(s[pos + 1])
    if chr1 == 0xc2:
        return chr2 == 0x85 or chr2 == 0xa0
    if chr1 == 0xe2:
        if chr2 == 0x81 and s[pos + 2] == '\x9f':
            return True
        if chr2 != 0x80:
            return False
        chr3 = ord(s[pos + 2])
        if chr3 >= 0x80 and chr3 <= 0x8a:
            return True
        if chr3 == 0xa9 or chr3 == 0xa8 or chr3 == 0xaf:
            return True
        return False
    if chr1 == 0xe1:
        chr3 = ord(s[pos + 2])
        if chr2 == 0x9a and chr3 == 0x80:
            return True
        if chr2 == 0xa0 and chr3 == 0x8e:
            return True
        return False
    if chr1 == 0xe3 and chr2 == 0x80 and s[pos + 2] == '\x80':
        return True
    return False

def utf8_in_chars(value, pos, chars):
    """Equivalent of u'x' in u'xyz', where the left-hand side is
    a single UTF-8 character extracted from the string 'value' at 'pos'.
    Only works if both 'value' and 'chars' are correctly-formed UTF-8
    strings.
    """
    end = next_codepoint_pos(value, pos)
    i = 0
    while i < len(chars):
        k = pos
        while value[k] == chars[i]:
            k += 1
            i += 1
            if k == end:
                return True
        i += 1
    return False


def _invalid_cont_byte(ordch):
    return ordch>>6 != 0x2    # 0b10

_invalid_byte_2_of_2 = _invalid_cont_byte
_invalid_byte_3_of_3 = _invalid_cont_byte
_invalid_byte_3_of_4 = _invalid_cont_byte
_invalid_byte_4_of_4 = _invalid_cont_byte

def _surrogate_bytes(ch1, ch2):
    return ch1 == 0xed and ch2 > 0x9f

@enforceargs(allow_surrogates=bool)
def _invalid_byte_2_of_3(ordch1, ordch2, allow_surrogates):
    return (ordch2>>6 != 0x2 or    # 0b10
            (ordch1 == 0xe0 and ordch2 < 0xa0)
            # surrogates shouldn't be valid UTF-8!
            or (ordch1 == 0xed and ordch2 > 0x9f and not allow_surrogates))

def _invalid_byte_2_of_4(ordch1, ordch2):
    return (ordch2>>6 != 0x2 or    # 0b10
            (ordch1 == 0xf0 and ordch2 < 0x90) or
            (ordch1 == 0xf4 and ordch2 > 0x8f))


def check_utf8(s, allow_surrogates, start=0, stop=-1):
    """Check that 's' is a utf-8-encoded byte string.

    Returns the length (number of chars) and flag or raise CheckError.
    If allow_surrogates is False, then also raise if we see any.
    Note also codepoints_in_utf8(), which also computes the length
    faster by assuming that 's' is valid utf-8.
    """
    res, flag = _check_utf8(s, allow_surrogates, start, stop)
    if res >= 0:
        return res, flag
    raise CheckError(~res)

def get_utf8_length_flag(s):
    """ Get the length and flag out of valid utf8. For now just calls check_utf8
    """
    return check_utf8(s, True)

@jit.elidable
def _check_utf8(s, allow_surrogates, start, stop):
    pos = start
    continuation_bytes = 0
    flag = FLAG_ASCII
    if stop < 0:
        end = len(s)
    else:
        end = stop
    while pos < end:
        ordch1 = ord(s[pos])
        pos += 1
        # fast path for ASCII
        if ordch1 <= 0x7F:
            continue

        if flag == FLAG_ASCII:
            flag = FLAG_REGULAR

        if ordch1 <= 0xC1:
            return ~(pos - 1), 0

        if ordch1 <= 0xDF:
            if pos >= end:
                return ~(pos - 1), 0
            ordch2 = ord(s[pos])
            pos += 1

            if _invalid_byte_2_of_2(ordch2):
                return ~(pos - 2), 0
            # 110yyyyy 10zzzzzz -> 00000000 00000yyy yyzzzzzz
            continuation_bytes += 1
            continue

        if ordch1 <= 0xEF:
            if (pos + 2) > end:
                return ~(pos - 1), 0
            ordch2 = ord(s[pos])
            ordch3 = ord(s[pos + 1])
            pos += 2

            if (_invalid_byte_2_of_3(ordch1, ordch2, allow_surrogates) or
                _invalid_byte_3_of_3(ordch3)):
                return ~(pos - 3), 0

            if allow_surrogates and _surrogate_bytes(ordch1, ordch2):
                flag = FLAG_HAS_SURROGATES
            # 1110xxxx 10yyyyyy 10zzzzzz -> 00000000 xxxxyyyy yyzzzzzz
            continuation_bytes += 2
            continue

        if ordch1 <= 0xF4:
            if (pos + 3) > end:
                return ~(pos - 1), 0
            ordch2 = ord(s[pos])
            ordch3 = ord(s[pos + 1])
            ordch4 = ord(s[pos + 2])
            pos += 3

            if (_invalid_byte_2_of_4(ordch1, ordch2) or
                _invalid_byte_3_of_4(ordch3) or
                _invalid_byte_4_of_4(ordch4)):
                return ~(pos - 4), 0
            # 11110www 10xxxxxx 10yyyyyy 10zzzzzz -> 000wwwxx xxxxyyyy yyzzzzzz
            continuation_bytes += 3
            continue

        return ~(pos - 1), 0

    assert pos == end
    assert pos - continuation_bytes >= 0
    return pos - continuation_bytes, flag

def reencode_utf8_with_surrogates(utf8):
    """ Receiving valid UTF8 which contains surrogates, combine surrogate
    pairs into correct UTF8 with pairs collpased. This is a rare case
    and you should not be using surrogate pairs in the first place,
    so the performance here is a bit secondary
    """
    s = StringBuilder(len(utf8))
    stop = len(utf8)
    i = 0
    while i < stop:
        uchr = codepoint_at_pos(utf8, i)
        if 0xD800 <= uchr <= 0xDBFF:
            high = uchr
            i = next_codepoint_pos(utf8, i)
            if i >= stop:
                unichr_as_utf8_append(s, uchr, True)
                break
            low = codepoint_at_pos(utf8, i)
            if 0xDC00 <= low <= 0xDFFF:
                uchr = 0x10000 + (high - 0xD800) * 0x400 + (low - 0xDC00)
                i = next_codepoint_pos(utf8, i)                
            # else not really a surrogate pair, just append high
        else:
            i = next_codepoint_pos(utf8, i)
        unichr_as_utf8_append(s, uchr, True)
    return s.build()


@jit.elidable
def codepoints_in_utf8(value, start=0, end=sys.maxint):
    """Return the number of codepoints in the UTF-8 byte string
    'value[start:end]'.  Assumes 0 <= start <= len(value) and start <= end.
    """
    if end > len(value):
        end = len(value)
    assert 0 <= start <= end
    length = 0
    for i in range(start, end):
        # we want to count the number of chars not between 0x80 and 0xBF;
        # we do that by casting the char to a signed integer
        signedchar = rffi.cast(rffi.SIGNEDCHAR, ord(value[i]))
        if rffi.cast(lltype.Signed, signedchar) >= -0x40:
            length += 1
    return length

@jit.elidable
def surrogate_in_utf8(value):
    """Check if the UTF-8 byte string 'value' contains a surrogate.
    The 'value' argument must be otherwise correctly formed for UTF-8.
    """
    for i in range(len(value) - 2):
        if value[i] == '\xed' and value[i + 1] >= '\xa0':
            return True
    return False


UTF8_INDEX_STORAGE = lltype.GcStruct('utf8_loc',
    ('flag', lltype.Signed),
    ('contents', lltype.Ptr(lltype.GcArray(lltype.Struct('utf8_loc_elem',
        ('baseindex', lltype.Signed),
        ('ofs', lltype.FixedSizeArray(lltype.Char, 16)),
    )))))

def get_flag_from_code(oc):
    assert isinstance(oc, int)
    if oc <= 0x7F:
        return FLAG_ASCII
    if 0xD800 <= oc <= 0xDFFF:
        return FLAG_HAS_SURROGATES
    return FLAG_REGULAR

def combine_flags(one, two):
    return one | two

FLAG_ASCII          = 0     # no bits
FLAG_REGULAR        = 1     # bit 0
FLAG_HAS_SURROGATES = 3     # bit 0 and bit 1
# note that we never need index storage if we're pure ascii, but it's useful
# for passing into W_UnicodeObject.__init__

#ASCII_INDEX_STORAGE_BLOCKS = 5
#ASCII_INDEX_STORAGE = lltype.malloc(UTF8_INDEX_STORAGE.contents.TO,
#                                    ASCII_INDEX_STORAGE_BLOCKS,
#                                    immortal=True)
#for _i in range(ASCII_INDEX_STORAGE_BLOCKS):
#    ASCII_INDEX_STORAGE[_i].baseindex = _i * 64
#    for _j in range(16):
#        ASCII_INDEX_STORAGE[_i].ofs[_j] = chr(_j * 4 + 1)

def null_storage():
    return lltype.nullptr(UTF8_INDEX_STORAGE)

UTF8_IS_ASCII = lltype.malloc(UTF8_INDEX_STORAGE, immortal=True)
UTF8_IS_ASCII.contents = lltype.nullptr(UTF8_INDEX_STORAGE.contents.TO)
UTF8_HAS_SURROGATES = lltype.malloc(UTF8_INDEX_STORAGE, immortal=True)
UTF8_HAS_SURROGATES.contents = lltype.nullptr(UTF8_INDEX_STORAGE.contents.TO)

def create_utf8_index_storage(utf8, utf8len):
    """ Create an index storage which stores index of each 4th character
    in utf8 encoded unicode string.
    """
#    if len(utf8) == utf8len < ASCII_INDEX_STORAGE_BLOCKS * 64:
#        return ASCII_INDEX_STORAGE
    arraysize = utf8len // 64 + 1
    storage = lltype.malloc(UTF8_INDEX_STORAGE)
    contents = lltype.malloc(UTF8_INDEX_STORAGE.contents.TO, arraysize)
    storage.contents = contents
    baseindex = 0
    current = 0
    while True:
        contents[current].baseindex = baseindex
        next = baseindex
        for i in range(16):
            if utf8len == 0:
                next += 1      # assume there is an extra '\x00' character
            else:
                next = next_codepoint_pos(utf8, next)
            contents[current].ofs[i] = chr(next - baseindex)
            utf8len -= 4
            if utf8len < 0:
                assert current + 1 == len(contents)
                break
            next = next_codepoint_pos(utf8, next)
            next = next_codepoint_pos(utf8, next)
            next = next_codepoint_pos(utf8, next)
        else:
            current += 1
            baseindex = next
            continue
        break
    return storage

@jit.dont_look_inside
def codepoint_position_at_index(utf8, storage, index):
    """ Return byte index of a character inside utf8 encoded string, given
    storage of type UTF8_INDEX_STORAGE.  The index must be smaller than
    or equal to the utf8 length: if needed, check explicitly before calling
    this function.
    """
    current = index >> 6
    ofs = ord(storage.contents[current].ofs[(index >> 2) & 0x0F])
    bytepos = storage.contents[current].baseindex + ofs
    index &= 0x3
    if index == 0:
        return prev_codepoint_pos(utf8, bytepos)
    elif index == 1:
        assert bytepos >= 0
        return bytepos
    elif index == 2:
        return next_codepoint_pos(utf8, bytepos)
    else:
        return next_codepoint_pos(utf8, next_codepoint_pos(utf8, bytepos))

@jit.dont_look_inside
def codepoint_at_index(utf8, storage, index):
    """ Return codepoint of a character inside utf8 encoded string, given
    storage of type UTF8_INDEX_STORAGE
    """
    current = index >> 6
    ofs = ord(storage.contents[current].ofs[(index >> 2) & 0x0F])
    bytepos = storage.contents[current].baseindex + ofs
    index &= 0x3
    if index == 0:
        return codepoint_before_pos(utf8, bytepos)
    if index == 3:
        bytepos = next_codepoint_pos(utf8, bytepos)
        index = 2     # fall-through to the next case
    if index == 2:
        bytepos = next_codepoint_pos(utf8, bytepos)
    return codepoint_at_pos(utf8, bytepos)

@jit.dont_look_inside
def codepoint_index_at_byte_position(utf8, storage, bytepos):
    """ Return the character index for which
    codepoint_position_at_index(index) == bytepos.
    This is a relatively slow operation in that it runs in a time
    logarithmic in the length of the string, plus some constant that
    is not tiny either.
    """
    index_min = 0
    index_max = len(storage.contents) - 1
    while index_min < index_max:
        index_middle = (index_min + index_max + 1) // 2
        base_bytepos = storage.contents[index_middle].baseindex
        if bytepos < base_bytepos:
            index_max = index_middle - 1
        else:
            index_min = index_middle
    bytepos1 = storage.contents[index_min].baseindex
    result = index_min << 6
    while bytepos1 < bytepos:
        bytepos1 = next_codepoint_pos(utf8, bytepos1)
        result += 1
    return result


def make_utf8_escape_function(pass_printable=False, quotes=False, prefix=None):
    @jit.elidable
    def unicode_escape(s):
        size = len(s)
        result = StringBuilder(size)

        if quotes:
            if prefix:
                result.append(prefix)
            if s.find('\'') != -1 and s.find('\"') == -1:
                quote = ord('\"')
                result.append('"')
            else:
                quote = ord('\'')
                result.append('\'')
        else:
            quote = 0

            if size == 0:
                return ''

        pos = 0
        while pos < size:
            oc = codepoint_at_pos(s, pos)
            ch = s[pos]

            # Escape quotes
            if quotes and (oc == quote or ch == '\\'):
                result.append('\\')
                next_pos = next_codepoint_pos(s, pos)
                result.append_slice(s, pos, next_pos)
                pos = next_pos
                continue

            # The following logic is enabled only if MAXUNICODE == 0xffff, or
            # for testing on top of a host Python where sys.maxunicode == 0xffff
            if (not we_are_translated() and sys.maxunicode == 0xFFFF and
                0xD800 <= oc < 0xDC00 and pos + 3 < size):
                # Map UTF-16 surrogate pairs to Unicode \UXXXXXXXX escapes
                pos += 3
                oc2 = codepoint_at_pos(s, pos)

                if 0xDC00 <= oc2 <= 0xDFFF:
                    ucs = (((oc & 0x03FF) << 10) | (oc2 & 0x03FF)) + 0x00010000
                    char_escape_helper(result, ucs)
                    pos += 3
                    continue
                # Fall through: isolated surrogates are copied as-is
                pos -= 3

            # Map special whitespace to '\t', \n', '\r'
            if ch == '\t':
                result.append('\\t')
            elif ch == '\n':
                result.append('\\n')
            elif ch == '\r':
                result.append('\\r')
            elif ch == '\\':
                result.append('\\\\')

            # Map non-printable or non-ascii to '\xhh' or '\uhhhh'
            elif pass_printable and not (oc <= 0x10ffff and unicodedb.isprintable(oc)):
                char_escape_helper(result, oc)
            elif not pass_printable and (oc < 32 or oc >= 0x7F):
                char_escape_helper(result, oc)

            # Copy everything else as-is
            else:
                if oc < 128:
                    result.append(ch)
                else:
                    next_pos = next_codepoint_pos(s, pos)
                    result.append_slice(s, pos, next_pos)
            pos = next_codepoint_pos(s, pos)

        if quotes:
            result.append(chr(quote))
        return result.build()

    TABLE = '0123456789abcdef'

    def char_escape_helper(result, char):
        if char >= 0x10000 or char < 0:
            result.append("\\U")
            zeros = 8
        elif char >= 0x100:
            result.append("\\u")
            zeros = 4
        else:
            result.append("\\x")
            zeros = 2
        for i in range(zeros-1, -1, -1):
            result.append(TABLE[(char >> (4 * i)) & 0x0f])

    return unicode_escape #, char_escape_helper

class Utf8StringBuilder(object):
    def __init__(self, size=0):
        self._s = StringBuilder(size)
        self._lgt = 0
        self._flag = FLAG_ASCII

    def append(self, s):
        # for strings
        self._s.append(s)
        newlgt, newflag = get_utf8_length_flag(s)
        self._lgt += newlgt
        self._flag = combine_flags(self._flag, newflag)

    @signature(char(), returns=none())
    def append_char(self, s):
        # for characters, ascii
        self._lgt += 1
        self._s.append(s)

    def append_code(self, code):
        self._flag = combine_flags(self._flag, get_flag_from_code(code))
        self._lgt += 1
        unichr_as_utf8_append(self._s, code, True)

    def append_utf8(self, utf8, length, flag):
        self._flag = combine_flags(self._flag, flag)
        self._lgt += length
        self._s.append(utf8)

    def build(self):
        return self._s.build()

    def get_flag(self):
        return self._flag

    def get_length(self):
        return self._lgt

class Utf8StringIterator(object):
    def __init__(self, utf8s):
        self._utf8 = utf8s
        self._end = len(utf8s)
        self._pos = 0

    def __iter__(self):
        return self

    def next(self):
        if self._pos == self._end:
            raise StopIteration
        ret = codepoint_at_pos(self._utf8, self._pos)
        self._pos = next_codepoint_pos(self._utf8, self._pos)
        return ret

def decode_latin_1(s):
    if len(s) == 0:
        return s
    if len(s) == 1 and ord(s[0]) <= 0x7F:
        return s
    try:
        check_ascii(s)
        return s
    except CheckError:
        return _decode_latin_1_slowpath(s)

def _decode_latin_1_slowpath(s):
    res = StringBuilder(len(s))
    i = 0
    while i < len(s):
        if ord(s[i]) > 0x7F:
            while i < len(s) and ord(s[i]) > 0x7F:
                unichr_as_utf8_append(res, ord(s[i]))
                i += 1
        else:
            start = i
            end = i + 1
            while end < len(s) and ord(s[end]) <= 0x7F:
                end += 1
            res.append_slice(s, start, end)
            i = end
    return res.build()
