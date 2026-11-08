import struct, sys

def exports(f):
    d = open(f, 'rb').read()
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    optsz = struct.unpack_from('<H', d, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from('<H', d, opt)[0]
    ddoff = opt + (96 if magic == 0x10b else 112)
    erva, esz = struct.unpack_from('<II', d, ddoff)
    secs = []
    for i in range(nsec):
        o = pe + 24 + optsz + i * 40
        vs = struct.unpack_from('<I', d, o + 8)[0]
        va = struct.unpack_from('<I', d, o + 12)[0]
        raw = struct.unpack_from('<I', d, o + 20)[0]
        secs.append((va, vs, raw))
    def r2o(r):
        for va, vs, raw in secs:
            if va <= r < va + vs:
                return raw + (r - va)
        return None
    eo = r2o(erva)
    nfun = struct.unpack_from('<I', d, eo + 20)[0]
    nname = struct.unpack_from('<I', d, eo + 24)[0]
    return nfun, nname

for f in sys.argv[1:]:
    try:
        nf, nn = exports(f)
        print("%-28s numFunctions=%d numNames=%d" % (f, nf, nn))
    except Exception as e:
        print("%-28s ERROR %s" % (f, e))
