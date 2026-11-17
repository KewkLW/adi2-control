import sys,struct
from dissect.util.compression import lzxpress_huffman
d=open(sys.argv[1],'rb').read(); out=sys.argv[2]
pos=0;n=0
while True:
    i=d.find(bytes.fromhex('0a51e5c0'),pos)
    if i<0: break
    pos=i+4
    hs=d[i+4]; alg=d[i+7]; usize,csz=struct.unpack_from('<QQ',d,i+8)
    print(hex(i),'hdr',d[i+4:i+8].hex(),'usize',usize,'chunk',csz)
    p=i+hs; res=b''
    try:
        while len(res)<usize:
            clen=struct.unpack_from('<I',d,p)[0]; p+=4
            blk=d[p:p+clen]; p+=clen
            want=min(csz,usize-len(res))
            res+= blk if clen==want else lzxpress_huffman.decompress(blk)[:want]
        fn=f'{out}/blob{n}_{hex(i)}.bin'; open(fn,'wb').write(res); print(' ->',fn,len(res),res[:4]); n+=1
    except Exception as e: print(' fail',e)
