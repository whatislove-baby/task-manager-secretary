/** Sample actual artwork alpha rather than its rectangular button bounds. */
export interface AlphaMask {width:number;height:number;alpha:Uint8Array}
export function alphaHit(mask:AlphaMask,x:number,y:number,radius=0):boolean {
 for(let dy=-radius;dy<=radius;dy++)for(let dx=-radius;dx<=radius;dx++){
  const px=Math.floor(x+dx),py=Math.floor(y+dy);
  if(px>=0&&py>=0&&px<mask.width&&py<mask.height&&mask.alpha[py*mask.width+px]>24)return true;
 }return false;
}
export function inversePoint(x:number,y:number,width:number,height:number,rect:{left:number;top:number},m:number[]){
 const [a,b,c,d]=m,det=a*d-b*c;if(Math.abs(det)<1e-8)return null;
 const minX=Math.min(0,a*width,c*height,a*width+c*height),minY=Math.min(0,b*width,d*height,b*width+d*height);
 const tx=x-rect.left+minX,ty=y-rect.top+minY;
 return {x:(d*tx-c*ty)/det,y:(a*ty-b*tx)/det};
}
const masks=new Map<string,AlphaMask|null>();
function maskFor(img:HTMLImageElement):AlphaMask|null {
 const src=img.currentSrc||img.src;if(masks.has(src))return masks.get(src)!;
 if(!img.complete||!img.naturalWidth)return null;
 try{const canvas=document.createElement('canvas');canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;const ctx=canvas.getContext('2d',{willReadFrequently:true});if(!ctx)return null;ctx.drawImage(img,0,0);const rgba=ctx.getImageData(0,0,canvas.width,canvas.height).data,alpha=new Uint8Array(canvas.width*canvas.height);for(let i=0;i<alpha.length;i++)alpha[i]=rgba[i*4+3];const mask={width:canvas.width,height:canvas.height,alpha};masks.set(src,mask);return mask;}catch{masks.set(src,null);return null;}
}
function matrixFor(element:Element):number[]{
 let result=[1,0,0,1];for(let node:Element|null=element;node;node=node.parentElement){const t=getComputedStyle(node).transform;const values=t.startsWith('matrix(')?t.slice(7,-1).split(',').map(Number):t.startsWith('matrix3d(')?(()=>{const v=t.slice(9,-1).split(',').map(Number);return [v[0],v[1],v[4],v[5]];})():[1,0,0,1];const [a,b,c,d]=values,[e,f,g,h]=result;result=[a*e+c*f,b*e+d*f,a*g+c*h,b*g+d*h];}return result;
}
function geometry(element:HTMLElement,x:number,y:number){const style=getComputedStyle(element),width=parseFloat(style.width)||element.offsetWidth,height=parseFloat(style.height)||element.offsetHeight;if(!width||!height)return null;return {point:inversePoint(x,y,width,height,element.getBoundingClientRect(),matrixFor(element)),width,height,style};}
function visible(element:Element,x:number,y:number){for(let n:Element|null=element;n;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity||1)<.05)return false;if(s.overflow==='hidden'){const r=n.getBoundingClientRect();if(x<r.left||x>r.right||y<r.top||y>r.bottom)return false;}}return true;}
const atlasImage=()=>{let image=(atlasImage as any).image as HTMLImageElement|undefined;if(!image){image=document.createElement('img');image.src='./art/raven-flight-atlas.png';(atlasImage as any).image=image;}return image;};
export function hoverAvailability(until:number,now:number,art:boolean,close:boolean,inside=true){
 const next=inside?(art?now+900:until):0;
 return {until:next,visible:inside&&(art||close||now<next),interactive:inside&&(art||close)};
}
export function birdPose(slot:number){
 const rotations=[-1.4,.7,-.4,1.2,-.8],scales=[.98,1.015,1,.99,1.02],durations=[6.1,7.3,6.7,8.1,7.7];
 const i=((slot%5)+5)%5;return {rotation:rotations[i],scale:scales[i],duration:durations[i],delay:-i*1.37};
}
export function artworkHit(root:HTMLElement,x:number,y:number,includeClose=true):boolean {
 // A visible source reader is a real modal surface, even though it is portalled outside the artwork root.
 const reader=root.ownerDocument.querySelector<HTMLElement>('.canvas-reader-overlay');
 if(reader&&visible(reader,x,y)){const r=reader.getBoundingClientRect();if(x>=r.left&&x<=r.right&&y>=r.top&&y<=r.bottom)return true;}
 for(const node of root.querySelectorAll<HTMLElement>('.crow-paper,.crow-context,.crow-name-tag,.crow-close,.crow-move-error,.crow-tooltip')){
  if(node.classList.contains('crow-close')&&!includeClose)continue;
  if(!visible(node,x,y)||(node.classList.contains('crow-close')&&getComputedStyle(node).pointerEvents==='none'))continue;
  const r=node.getBoundingClientRect();if(x>=r.left&&x<=r.right&&y>=r.top&&y<=r.bottom)return true;
 }
 for(const image of root.querySelectorAll<HTMLImageElement>('.crow-branch,.crow-illustration')){
  if(!visible(image,x,y))continue;const g=geometry(image,x,y),mask=maskFor(image);if(!g?.point||!mask)continue;
  let width=g.width,height=g.height,ox=0,oy=0;
  if(g.style.objectFit==='contain'){const scale=Math.min(width/mask.width,height/mask.height);const w=mask.width*scale,h=mask.height*scale;ox=(width-w)/2;oy=(height-h)/2;width=w;height=h;}
  const px=(g.point.x-ox)/width*mask.width,py=(g.point.y-oy)/height*mask.height;
  if(alphaHit(mask,px,py,Math.ceil(1.25*mask.width/width)))return true;
 }
 for(const sprite of root.querySelectorAll<HTMLElement>('.crow-flight-sprite')){
  if(!visible(sprite,x,y))continue;const g=geometry(sprite,x,y),mask=maskFor(atlasImage());if(!g?.point||!mask||g.point.x<0||g.point.y<0||g.point.x>g.width||g.point.y>g.height)continue;
  const position=g.style.backgroundPosition.split(' ').map(parseFloat),col=Math.round((position[0]||0)/100*3),row=Math.round((position[1]||0)/100);
  if(alphaHit(mask,(col+g.point.x/g.width)*mask.width/4,(row+g.point.y/g.height)*mask.height/2,1))return true;
 }
 for(const path of root.querySelectorAll<SVGGeometryElement>('.crow-pendant path')){
  if(!visible(path,x,y))continue;try{const ctm=path.getScreenCTM();if(!ctm)continue;const local=new DOMPoint(x,y).matrixTransform(ctm.inverse()),style=getComputedStyle(path);if(style.fill!=='none'&&path.isPointInFill(local)||style.stroke!=='none'&&path.isPointInStroke(local))return true;}catch{}
 }
 return false;
}
