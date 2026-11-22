# 自然手记 · 悬浮窗美术方案

本轮依据用户要求：缩小悬浮窗，偏简笔画、水彩或彩铅质感且克制。设计由高性能模型复核，实施由Agent完成；不计入学员独立掌握。

## 统一规则

收起380×154，展开380×560。单鸟约56×76，靛灰蓝/石墨彩铅，保留真实喙形与微小眼部高光；细枝、少量鼠尾草叶，取消粗黑卡通线与大白眼。纸笺用暖白、细灰铅笔边、少量灰桃色，保持正文清晰。默认仅图案，原有列表/详情互斥、完成动画、数据同步保持。

## Skill调研与实际工具

GitHub调研：[Anthropic canvas-design](https://github.com/anthropics/skills/tree/main/skills/canvas-design)（静态作品构图/材质工作流）与现有frontend-design。canvas-design未全局安装，不执行仓库代码；本项目不是静态海报，采用其克制留白与材质一致性思路，交互仍按项目要求。

实际图片使用本会话内置 imagegen 工具生成，透明输出；不是CLI或用户云端API调用。原图复制到src/renderer/public/art，由Vite作为发布资源复制；未使用Python重绘/去背景。历史参考与许可见art-sources.md，参考图未作为本次imagegen的输入，成品PNG不能标记为历史公有领域原图。

## 实际提示词

### raven-pencil.png

Use case: illustration-story. Create a production desktop widget asset: exactly ONE full-body small raven perched, facing right in a calm 3/4 side profile. Truly transparent background, isolated, no ground, no branch, no shadow, no text, no frame. Refined contemporary naturalist sketchbook illustration: loose fine graphite and colored-pencil contours with restrained indigo-charcoal watercolor washes, subtle visible pigment granulation and dry-brush edges WITHIN the bird, slate-blue highlights, elegant feather marks. Sophisticated quiet aesthetic, NOT thick black vector outlines, NOT glossy 3D, NOT sticker border, NOT large cartoon white eyes. Small dark intelligent eye with one tiny cream glint, gentle character but credible raven anatomy and long slender beak. Feet and claws fully visible and aligned on same baseline, distinct silhouette readable when scaled to 55 pixels wide. Tail points slightly left and down, wings folded, head modestly oversized only 10%. Muted ink blue / charcoal / soft graphite palette. Entire bird centered and fully inside frame with modest transparent margin. Deliver a single raven only, no other objects.

### branch-pencil.png

Asset type: production transparent desktop widget illustration, companion to an indigo-charcoal colored-pencil raven. A single delicate horizontal bare woodland twig, long and slender, full object visible from left tip to right tip, centered across a wide composition, isolated on TRUE transparent background. Restrained graphite linework with muted warm grey-brown and slate blue watercolor pencil shading, subtle soft pigment grain within strokes. Natural slight upward curve, three tiny offshoots, exactly two very small desaturated sage leaves near far right. Large clear empty space above for birds, no birds in this image, no text, no white paper background, no landscape, no shadow, no frame, no rope, no UI. Main twig nearly level so many small birds can perch evenly. Refined minimalist nature sketch, airy and understated, not thick black cartoon outline, no angular beam or log. Branch itself occupies narrow middle horizontal band, not a large diagonal bough. Elegant adult illustrated stationery aesthetic.


## 展翅动作帧补充

使用内置imagegen，以现有彩铅乌鸦为参考生成并再编排八个独立翅膀姿态，最终PNG为public/art/raven-flight-atlas.png。精确提示词见flight-art-prompts.json。使用4列×2行图集逐帧切换（抬翅、展开、下拍、回收），按两行躯干基线做显示校准；原素材PNG未用Python裁剪/重绘。飞走与盘旋共用动画内层，外层分别控制起飞和环绕轨迹，减少动态效果模式停用动画。
