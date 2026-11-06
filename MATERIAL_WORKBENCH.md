# 素材采集工作台

本项目保留原有视觉中国和壁纸汇采集能力，并增加猫狗图片来源。所有来源共用同一套下载进程、线程池、图片校验和日志机制。

## 来源

| 显示名称 | ID | 说明 |
|---|---|---|
| 壁纸汇（最高直链画质） | `bizhihui` | 原有来源，按详情页解析最高直链 |
| 视觉中国（需验证） | `vcg` | 原有来源，使用内嵌网页完成安全验证 |
| CATAAS 猫图片 | `cataas` | 公开猫图片接口，统一请求 1000×1000 |
| TheDogAPI 狗图片 | `thedogapi` | 公开狗图片接口，按原图尺寸下载 |
| 视频封面（yt-dlp） | `ytdlp` | 在关键词框输入视频 URL，提取视频封面 |
| 图片站点（gallery-dl） | `gallery-dl` | 在关键词框输入 gallery-dl 支持的图片站点 URL |

## 素材清单

每个保存目录会生成：

- `manifest.jsonl`：来源、类别、原始 URL、文件名、尺寸、格式、SHA-256、抓取时间和许可证复核状态
- `manifest_summary.json`：本次任务的目标数量、完成数量、跳过数量、失败数量、新增清单记录数、实际请求数、冷却次数和来源策略
- `request_log.jsonl`：请求响应、异常、限速等待、冷却等待和断路器事件审计记录

## 来源保护

所有来源共用请求保护层：来源级最大并发、最小请求间隔、随机抖动、`Retry-After` 重试、连续失败冷却和指数退避断路器。图片流在完整读取期间占用并发闸门，避免大图下载绕过并发限制。

保护层不会绕过视觉中国的安全验证；检测到验证页时会停止该来源并提示用户在内嵌浏览器中完成验证。CATAAS 的 500 响应不自动重复请求，因为该来源的 500 常由 GIF 或大图服务端处理超时造成。

许可证字段使用 `source-license-to-review`。来源接口返回的图片不能自动视为 CC0 或商业可用内容，公开发布或生产使用前需要复核授权。

## 使用方式

1. 在“图片来源”中选择来源
2. 输入关键词；CATAAS 和 TheDogAPI 当前按随机图片接口采集，关键词会记录到清单
3. 设置格式、数量、线程和保存路径
4. 点击“开始下载”

视觉中国仍会弹出安全验证窗口。壁纸汇、CATAAS 和 TheDogAPI 直接使用下载子进程。
视频封面来源需要在关键词框输入视频 URL，并依赖 `yt-dlp`。
图片站点来源需要在关键词框输入图片站点 URL，并依赖 `gallery-dl`。

## 开发验证

```bash
python -m py_compile vcg.py ui_main.py
python -m pip install -r requirements.txt
```

可用临时目录验证猫狗来源：

```bash
python - <<'PY'
import tempfile
from vcg import Download, SOURCE_CATAAS, SOURCE_THEDOGAPI

for source in (SOURCE_CATAAS, SOURCE_THEDOGAPI):
    worker = Download("", 2, "jpg", source=source, download_workers=2)
    worker.save_file_path = tempfile.mkdtemp(prefix="vcg-material-")
    worker.save_img_to_local()
PY
```
