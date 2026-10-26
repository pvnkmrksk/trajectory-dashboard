/* Recorded data clock adapter. Analysis local-time columns remain untouched. */
export function decodeReplayTable(buffer) {
  const length = new DataView(buffer).getUint32(0, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));
  const body = 4 + length + (header.bodyPadding || 0), data = {};
  const types = {'<f4': Float32Array, '=f4': Float32Array, '<f8': Float64Array, '=f8': Float64Array,
    '<u4': Uint32Array, '=u4': Uint32Array, '<u2': Uint16Array, '=u2': Uint16Array, '|u1': Uint8Array, '|i1': Int8Array};
  for (const [name, d] of Object.entries(header.arrays)) {
    if (!types[d.dtype]) throw new Error(`Unsupported recorded column ${d.dtype}`);
    data[name] = new types[d.dtype](buffer, body + d.offset, d.length);
  }
  return {header, data};
}

export function buildReplayProduct(header, data, originTicks, budget = 240000) {
  if (!data.replayTime || !header.replayTimeline?.originTicks) throw new Error('This compiled dataset has no global recorded clock; update the viewer.');
  const shift = Number(BigInt(header.replayTimeline.originTicks) - BigInt(originTicks)) / 1e7;
  const rows = data.x.length, stride = Math.max(1, Math.ceil(rows / Math.max(1, budget)));
  const links = [], tracks = new Map(); let previous = -1;
  let xmin = Infinity, xmax = -Infinity, zmin = Infinity, zmax = -Infinity;
  for (let row = 0; row < rows; row++) {
    const seg = data.segment[row], t = data.replayTime[row] + shift;
    const scene = String(header.categories.scene?.[data.segmentScene[seg]] || '').toLowerCase();
    const file = String(header.categories.file?.[data.segmentFile[seg]] || '').toLowerCase();
    if (scene === 'controlscene' || scene === 'replayscene' || /_(controlscene|replayscene)_/.test(file) || !Number.isFinite(t) || t < 0 || !Number.isFinite(data.x[row]) || !Number.isFinite(data.z[row])) { previous = -1; continue; }
    const first = row === 0 || data.segment[row - 1] !== seg;
    const last = row === rows - 1 || data.segment[row + 1] !== seg;
    if (!first && !last && row % stride) continue;
    const animal = data.segmentAnimal[seg], name = header.categories.animal?.[animal] || String(animal);
    const point = {t, x:data.x[row], z:data.z[row], yaw:data.orientation?.[row] || 0, seg, color:animal % 16, name};
    if (!tracks.has(animal)) tracks.set(animal, []);
    tracks.get(animal).push(point);
    xmin=Math.min(xmin,point.x);xmax=Math.max(xmax,point.x);zmin=Math.min(zmin,point.z);zmax=Math.max(zmax,point.z);
    if (previous >= 0 && data.segment[previous] === seg && data.replayTime[previous] <= data.replayTime[row]) links.push([previous,row]);
    previous = row;
  }
  for (const track of tracks.values()) track.sort((a,b)=>a.t-b.t);
  const n=links.length, vertices=new Float32Array(n*4),times=new Float32Array(n*2),panels=new Uint16Array(n*2),colors=new Uint8Array(n*2),animals=new Uint16Array(n*2),segments=new Uint32Array(n*2),samples=new Float32Array(n*2);
  links.forEach(([a,b],i)=>{const seg=data.segment[a], animal=data.segmentAnimal[seg];vertices.set([data.x[a],data.z[a],data.x[b],data.z[b]],i*4);times.set([data.replayTime[a]+shift,data.replayTime[b]+shift],i*2);colors[i*2]=colors[i*2+1]=animal%16;animals[i*2]=animals[i*2+1]=animal;segments[i*2]=segments[i*2+1]=seg;});
  const product={vertices,times,panels,colors,animals,segments,samples,links:n,segmentCount:data.segmentTrial.length,segmentLabels:header.segmentIds,
    panelCount:1,panelNames:['Recorded trajectories'],animalNames:header.categories.animal,columns:1,
    bounds:Number.isFinite(xmin)?{xmin,xmax,zmin,zmax}:{xmin:-1,xmax:1,zmin:-1,zmax:1},rois:[],currentPoses:[]};
  return {product,tracks,retainedPoints:[...tracks.values()].reduce((n,t)=>n+t.length,0)};
}

export function replayCursors(tracks, seconds) {
  const poses=[];
  for (const points of tracks.values()) {
    if (!points.length || seconds < points[0].t || seconds > points.at(-1).t) continue;
    let lo=0,hi=points.length-1;
    while(lo<hi){const mid=(lo+hi+1)>>1;if(points[mid].t<=seconds)lo=mid;else hi=mid-1;}
    const a=points[lo],b=points[lo+1];let f=0;
    if(b&&b.seg===a.seg&&b.t>a.t)f=Math.max(0,Math.min(1,(seconds-a.t)/(b.t-a.t)));
    const delta=b?((b.yaw-a.yaw+540)%360)-180:0;
    poses.push({...a,panel:0,x:b?a.x+(b.x-a.x)*f:a.x,z:b?a.z+(b.z-a.z)*f:a.z,yaw:a.yaw+delta*f,stale:seconds-a.t>1&&f===0});
  }
  return poses;
}
