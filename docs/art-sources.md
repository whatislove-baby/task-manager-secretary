# 乌鸦悬浮窗美术来源与素材说明

核对日期：2026-10-29。

## 最终应用素材与历史参考的区别

本轮成品使用AI生成的原创PNG图案；下面列出的历史作品仅用作乌鸦形体、水彩/彩铅质感及自然细枝的参考。成品不是公有领域原图，也不因参考了公有领域作品就被宣称为CC0或历史作品。原始参考保存在`docs/art-references/`，不作为运行时UI素材。

## 公开领域参考

### Naumann · Corvus corax corax

- 原页面：https://commons.wikimedia.org/wiki/File:Corvus_corax_corax_(Kpjas).jpg
- 原始文件：https://upload.wikimedia.org/wikipedia/commons/e/e0/Corvus_corax_corax_%28Kpjas%29.jpg
- 本地文件：`docs/art-references/naumann-raven.jpg`，678×892，109030字节。
- 作者/作品来源：Naumann，《Naturgeschichte der Vögel Mitteleuropas》，1905年版或更早作品。
- 页面许可：PD-old-100-expired，Public Domain Mark 1.0。许可说明原作品版权已届满，在来源国及作者终身加100年及以下保护期的地区为公有领域；在美国因1931年前发表亦属公有领域。
- 标记链接：https://creativecommons.org/publicdomain/mark/1.0/
- 实际用途：渡鸦侧面轮廓、喙、尾与栖枝姿态参考；未裁图作为UI成品。

### Jacques Le Moyne de Morgues · A Kingfisher on a Branch

- 原页面：https://www.metmuseum.org/art/collection/search/364536
- 原始文件：https://collectionapi.metmuseum.org/api/collection/v1/iiif/364536/756544/main-image
- 本地文件：`docs/art-references/le-moyne-kingfisher-branch.jpg`，117412字节。
- 作者/年代/媒材：Jacques Le Moyne de Morgues，16世纪，水彩、水粉及黑色粉笔底稿。
- 页面明确标记Public Domain；The Met Open Access允许无需许可或费用用于商业和非商业用途；其政策页进一步明确公有领域作品图像以CC0开放。
- Open Access政策：https://www.metmuseum.org/hubs/open-access
- 实际用途：纤细枯枝、彩铅细线、水彩低饱和色层与留白参考；没有将翠鸟用作产品中的乌鸦。

两份参考均已下载并通过图像查看工具检视。没有将网页整体CC BY-SA文本许可误当成图片许可，也没有采用只有“免费”而无清楚授权的图片。

## GitHub绘图Skill调研

- 候选：Anthropic `canvas-design`。
- 入口：https://github.com/anthropics/skills/blob/main/skills/canvas-design/SKILL.md
- 适用范围：先形成视觉方向，再创作静态PNG/PDF作品，重视构图、材质、留白与克制的文字。
- 本轮取舍：作为美术流程调研候选；其静态海报工作流不直接替换Electron/React交互实现。不声称已安装该skill，未执行远程脚本或代码。
- 交互实现仍沿用已有窗口、组件、完成动画与编辑保护；美术素材生成使用当前内置图像生成能力。
