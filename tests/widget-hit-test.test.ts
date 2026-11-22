import {test} from 'node:test';
import assert from 'node:assert/strict';
import {alphaHit,inversePoint,hoverAvailability,birdPose} from '../src/renderer/widget-hit-test';

test('透明像素穿透，只有真实alpha与细枝有限容差命中，不以贴图矩形吞点击',()=>{
 const alpha=new Uint8Array(25);alpha[12]=255;const mask={width:5,height:5,alpha};
 assert.equal(alphaHit(mask,0,0),false);assert.equal(alphaHit(mask,2,2),true);
 assert.equal(alphaHit(mask,1,2,1),true);assert.equal(alphaHit(mask,0,0,1),false);
 assert.equal(alphaHit(mask,-10,2,1),false);alpha[0]=10;assert.equal(alphaHit(mask,0,0),false);
});

test('旋转和缩放图案从屏幕坐标反算真实像素，动画不会扩大命中矩形',()=>{
 const point=inversePoint(120,30,10,20,{left:100,top:20},[0,2,-2,0]);
 assert.ok(point);assert.equal(point.x,5);assert.equal(point.y,10);
 const outside=inversePoint(142,42,10,20,{left:100,top:20},[0,2,-2,0]);
 assert.ok(outside);assert.ok(outside.x>10||outside.y<0);
 assert.equal(inversePoint(0,0,10,20,{left:0,top:0},[0,0,0,0]),null);
});


test('关闭按钮保持可达但空白立即穿透，按钮悬停不永久延长显示',()=>{
 const entered=hoverAvailability(0,100,true,false);assert.equal(entered.visible,true);assert.equal(entered.interactive,true);
 const gap=hoverAvailability(entered.until,500,false,false);assert.equal(gap.visible,true);assert.equal(gap.interactive,false);
 const close=hoverAvailability(gap.until,1200,false,true);assert.equal(close.visible,true);assert.equal(close.interactive,true);assert.equal(close.until,entered.until);
 const leftClose=hoverAvailability(close.until,1250,false,false);assert.equal(leftClose.visible,false);assert.equal(leftClose.interactive,false);
 const leftWindow=hoverAvailability(entered.until,200,false,false,false);assert.equal(leftWindow.visible,false);assert.equal(leftWindow.until,0);
});

test('五个枝头槽位保持各自微姿态，重新进入空位不改变其他鸟的姿态',()=>{
 const before=[0,1,2,3,4].map(birdPose);assert.equal(new Set(before.map(p=>p.rotation)).size,5);
 for(const slot of [0,2,3,4])assert.deepEqual(birdPose(slot),before[slot]);
 assert.deepEqual(birdPose(1),before[1]);
 assert.ok(before.every(p=>p.scale>=.98&&p.scale<=1.02&&Math.abs(p.rotation)<=1.5));
});
