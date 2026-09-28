(function(root){
  'use strict';
  function dimensions(buffer){
    const v=new DataView(buffer),n=v.byteLength;
    const text=(offset,size)=>Array.from(new Uint8Array(buffer,offset,size),x=>String.fromCharCode(x)).join('');
    if(n>=24&&v.getUint32(0)===0x89504e47)return [v.getUint32(16),v.getUint32(20)];
    if(n>=10&&text(0,3)==='GIF')return [v.getUint16(6,true),v.getUint16(8,true)];
    if(n>=30&&text(0,4)==='RIFF'&&text(8,4)==='WEBP'){
      const kind=text(12,4),u24=p=>v.getUint8(p)+(v.getUint8(p+1)<<8)+(v.getUint8(p+2)<<16);
      if(kind==='VP8X')return [u24(24)+1,u24(27)+1];
      if(kind==='VP8 ')return [v.getUint16(26,true)&16383,v.getUint16(28,true)&16383];
      if(kind==='VP8L'){const bits=v.getUint32(21,true);return [(bits&16383)+1,((bits>>>14)&16383)+1]}
    }
    if(n>=4&&v.getUint16(0)===0xffd8){
      let p=2;
      while(p+4<=n){
        if(v.getUint8(p++)!==255)break;
        while(p<n&&v.getUint8(p)===255)p++;
        if(p+3>n)break;const marker=v.getUint8(p++),size=v.getUint16(p);
        if(size<2||p+size>n)break;
        if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)&&size>=7)return [v.getUint16(p+5),v.getUint16(p+3)];
        p+=size;
      }
    }
    if(n>=20&&text(4,4)==='ftyp'){
      let largest=null;
      for(let p=8;p+16<=n;p++)if(v.getUint32(p)===0x69737065){
        const size=[v.getUint32(p+8),v.getUint32(p+12)];
        if(!largest||size[0]*size[1]>largest[0]*largest[1])largest=size;
      }
      return largest;
    }
    return null;
  }
  async function check(file){
    const size=dimensions(await file.slice(0,512*1024).arrayBuffer());
    if(!size||!size[0]||!size[1])throw Error('No se pudo verificar el tamaño de la imagen. Exporta una copia JPG o PNG.');
    if(size[0]*size[1]>24000000)throw Error('La imagen supera 24 megapíxeles. Elige una copia de menor resolución.');
    return size;
  }
  const api={dimensions,check};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.HscPhotoPreflight=api;
})(typeof globalThis!=='undefined'?globalThis:this);
