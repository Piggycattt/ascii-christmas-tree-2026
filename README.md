# Python 3D ASCII 字符圣诞树

这一版的动画主体已经改成**纯 Python 主渲染**，不再是“JavaScript 做动画、Python 只负责截图”。

- **NumPy**：生成 3D 字符点云、逐层旋转、透视投影和深度排序
- **Pillow**：预生成 ASCII 字符图集、合成字符、发光、飘雪与祝福文字
- **FFmpeg**：编码 20 秒、1080×1920、30fps 的 H.264 MP4
- **HTML**：默认播放 Python 成片；另保留一个可拖动的网页交互预览

## 这版怎样体现“转动有层次”

树冠不是一个整体圆锥一起转，而是拆成 10 个水平枝层。每层都有独立的：

- `layer`：枝层编号
- `layer_phase`：初始角度
- `layer_speed`：旋转速度
- 不对称枝梢轮廓（3、5、2 组叶瓣叠加）
- 分层锯齿半径曲线
- 旋转后的深度值
- 前后遮挡、明暗和字符密度

因此连续转动时，不同枝层的轮廓、亮面和遮挡会发生相对变化，不再像一张平面树形贴图转盘。

## 文件说明

- `python_ascii_tree.py`：**Python 主渲染器**，最重要的源文件
- `render_video.py`：兼容入口，运行后调用 Python 主渲染器
- `index.html`：默认播放 Python MP4 的公开分享网页
- `interactive-preview.html`：保留的 JavaScript 交互预览，可用鼠标拖动
- `poster.jpg`：网页视频封面
- `ASCII-Christmas-Tree-20s-1080x1920.mp4`：最终成片
- `打开网页.cmd`：双击打开分享网页
- `versions/`：旧版备份，方便回退

## 最简单的打开方式

双击：

```text
打开网页.cmd
```

网页会自动循环播放 Python 版 MP4。鼠标移到画面下方可以暂停、重播、下载 Python 源码或进入交互预览。

## 新手修改文字和外观

用记事本或 VS Code 打开 `python_ascii_tree.py`，最上方有 `CONFIG`：

```python
CONFIG = {
    "tree_layers": 10,          # 树冠层数
    "tree_height": 1190,        # 树高
    "tree_radius": 455,         # 树宽
    "rotation_speed": 0.43,     # 基础转速
    "spiral_turns": 5.6,        # 金色灯带圈数
    "greeting": "MERRY CHRISTMAS 2026",
    "dedication": "TO THE ONE I WANT TO SPEND CHRISTMAS WITH",
}
```

只修改数字或引号里的文字即可，不要删除逗号和引号。

## 先生成预览图

在本文件夹打开终端，运行：

```bash
python python_ascii_tree.py --preview
```

生成内容在 `previews/`：

- `python-tree-contact-sheet.jpg`：8 个关键时刻
- `python-tree-layer-motion.jpg`：每 0.25 秒一帧，专门检查分层转动

## 重新导出 20 秒 MP4

运行：

```bash
python render_video.py
```

或直接运行：

```bash
python python_ascii_tree.py --render
```

脚本会：

1. 使用多个 Python 进程渲染 600 帧；
2. 用 FFmpeg 编码 H.264；
3. 用 FFprobe 验证分辨率、时长、帧率、像素格式和帧数；
4. 用 FFmpeg 完整解码检查；
5. 成功后删除临时帧。

中途停止后，可以继续：

```bash
python python_ascii_tree.py --render --resume
```

## 成片技术参数

- 分辨率：1080 × 1920（9:16）
- 时长：20 秒
- 帧率：30 fps
- 总帧数：600
- 编码：H.264 / yuv420p / BT.709
- 音频：无
- 随机种子：固定，重复导出结果一致

## 旧版备份

当前 Python 重制前的 JavaScript 红灯球版本保存在：

```text
versions/v2-js-red-bulbs/
```

更早版本也保留在 `versions/` 中。
