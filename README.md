# 3D ASCII 字符圣诞树动画

这是一个**单文件、可修改、无需联网**的 3D ASCII 圣诞树网页。

- 树、星星、树干、树根和礼物均由 ASCII 字母与符号组成
- 绿色/青色多层树体会绕垂直轴旋转
- 金色螺旋灯带会逐段点亮并流动闪烁，并悬挂红色 ASCII 灯球
- 星星和礼物拥有真实 3D 厚度
- 保留飘雪、祝福文字和 20 秒完整时间线
- 网页支持鼠标/触控拖动旋转

## 最简单的打开方式

双击 `index.html` 即可。

也可以双击 `打开网页.cmd`。

## 操作

- **拖动**：手动旋转视角
- **空格**：暂停 / 继续
- **R**：从头播放
- **S**：保存当前画面
- 鼠标移到画面下方可看到按钮

## 修改祝福文字

用记事本或 VS Code 打开 `index.html`，找到靠前位置的 `CONFIG`：

```js
greeting: "MERRY CHRISTMAS 2026",
dedication: "TO THE ONE I WANT TO SPEND CHRISTMAS WITH",
```

修改引号内的文字后保存并刷新网页即可。为了保持纯 ASCII 风格，建议使用英文字母、数字和常见符号。

还可以修改：

```js
rotationSpeed: 0.34,     // 转速
seed: 20261225,          // 随机造型种子
treeHeight: 1280,        // 树高
treeRadius: 575,         // 树冠宽度
```

## 重新导出 20 秒 MP4

在本文件夹打开终端，运行：

```bash
uv run --with playwright python render_video.py
```

脚本会：

1. 用本机 Chrome 逐帧渲染 600 帧；
2. 使用 FFmpeg 编码为 H.264；
3. 自动验证时长、分辨率、帧率和像素格式；
4. 成功后删除临时帧。

输出文件：

```text
ASCII-Christmas-Tree-20s-1080x1920.mp4
```

## 文件说明

- `index.html`：完整可编辑网页，CSS/JavaScript 全部内嵌
- `render_video.py`：确定性逐帧视频导出脚本
- `打开网页.cmd`：Windows 一键打开网页
- `ASCII-Christmas-Tree-20s-1080x1920.mp4`：最终 20 秒竖屏视频

## 技术参数

- 画面：1080 × 1920（9:16）
- 时长：20 秒
- 帧率：30 fps
- 视频：H.264 / yuv420p / BT.709
- 音频：无（静音视频）
- 网页：原生 HTML Canvas，无第三方 CDN
