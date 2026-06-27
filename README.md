# AI人类文明科普短剧工作流

本项目用于搭建一个长期可持续的 AI 历史科普短剧工作台。核心定位是：

> 这不是讲某个国家的历史，而是讲人类如何一步步发明文明。

项目从智人走出非洲开始，沿着人类扩散、定居、农业、村落、城市、文字、国家、帝国、宗教、贸易、工业和现代社会等关键节点，讲述人类如何在不同土地上通过生存、协作、环境、技术、权力和信仰，逐步创造出新的社会形态。

完整主旨、叙事原则、选题标准和视觉风格见 [项目主旨要点.md](./项目主旨要点.md)。

## 当前能力

- 本地 Web 工作站：上传和扫描 PDF、EPUB、TXT、Markdown、MOBI、DOC、DOCX 等材料。
- 素材拆分：按 PDF 书签、EPUB spine、文本标题或文档结构拆成章节、chunk 和审查报告。
- 章节精读：调用 DeepSeek 对章节做阅读化整理，输出可审查的 JSON、Markdown 和 HTML。
- 时间线生成：基于材料章节提取时间、地点、事件、证据说明和短剧价值。
- 剧本生成：选择主题、年份范围和材料时间线后，调用短剧 Agent 生成源材料约束下的中文讲述型短剧。
- 剧本阅读器：查看已生成剧本，并在选中文本后使用对话助手做局部讨论和修改建议。
- Natural Earth 地图 API：提供现代地理底图、区域列表和 PNG 渲染接口，用于后续历史视觉开发。

## 目录结构

```text
.
├── README.md
├── 项目主旨要点.md
├── 剧本规划/
│   ├── 世界史短剧72集规划.csv
│   └── 世界史短剧72集规划.md
├── 资料库/
│   └── 索引/
│       ├── SAB文明史书单访问清单.md
│       └── 资料获取策略.md
└── drama-agent-system/
    ├── run_workbench.py
    ├── run_map_api.py
    ├── drama_agents/
    ├── data/natural_earth/
    └── tests/
```

本地上传文件、拆分结果、SQLite 数据库和生成脚本默认写入 `uploads/` 与 `drama-agent-system/outputs/`，这些运行产物已经在 `.gitignore` 中排除。

## 拉取代码

第一次拉取：

```bash
cd /Users/liuxinkun/Downloads/projects
git clone https://github.com/Xinkun666/ai-short-drama-workflow.git "AI短剧工作流"
cd "AI短剧工作流"
```

已有目录时更新：

```bash
cd /Users/liuxinkun/Downloads/projects/AI短剧工作流
git pull --ff-only origin main
```

如果本地有未提交修改，先查看差异再更新：

```bash
git status --short
git diff
```

## 环境搭建

建议使用 Python 3.9+。当前本机常用解释器是：

```bash
/Users/liuxinkun/opt/anaconda3/bin/python
```

创建并进入虚拟环境：

```bash
cd /Users/liuxinkun/Downloads/projects/AI短剧工作流
/Users/liuxinkun/opt/anaconda3/bin/python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

安装运行和测试依赖：

```bash
python -m pip install flask pypdf matplotlib pillow pytest
```

可选工具：

- macOS 自带 `textutil`：用于把 `.doc` 转为纯文本。
- Calibre 的 `ebook-convert`：用于处理 `.mobi`；没有安装时，建议先把 MOBI 转成 EPUB、TXT 或 Markdown。

## DeepSeek API 配置

AI 精读、时间线生成、剧本生成和剧本对话助手都依赖 `DEEPSEEK_API_KEY`。不要把真实密钥写入仓库。

临时只在当前终端生效：

```bash
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
```

长期写入 macOS zsh 配置：

```bash
nano ~/.zshrc
```

在文件末尾加入：

```bash
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
```

保存后让当前终端生效：

```bash
source ~/.zshrc
```

确认变量已存在，但不要打印完整密钥：

```bash
python - <<'PY'
import os
key = os.environ.get("DEEPSEEK_API_KEY", "")
print("DEEPSEEK_API_KEY:", "已配置" if key else "未配置")
print("长度:", len(key))
PY
```

`run_workbench.py` 启动时会先检查当前进程环境；如果没有 `DEEPSEEK_API_KEY`，会尝试从 `~/.zshrc` 读取 `export DEEPSEEK_API_KEY=...`，并只打印 `loaded from local shell config`，不会输出密钥内容。

可选 DeepSeek 调参变量：

```bash
export DEEPSEEK_MODEL="deepseek-v4-pro"
export DEEPSEEK_REFINER_MODEL="deepseek-v4-pro"
export DEEPSEEK_TIMELINE_MODEL="deepseek-v4-pro"
export DEEPSEEK_SCRIPT_MODEL="deepseek-v4-pro"
export DEEPSEEK_MAX_WORKERS="3"
export DEEPSEEK_TIMEOUT="240"
export DEEPSEEK_TIMELINE_MAX_CHARS="18000"
export SCRIPT_AGENT_MAX_TOKENS="14000"
export SCRIPT_REVIEW_MAX_TOKENS="3200"
```

## 启动工作站

```bash
cd /Users/liuxinkun/Downloads/projects/AI短剧工作流/drama-agent-system
python run_workbench.py --host 127.0.0.1 --port 8765 --workspace .. --outputs outputs
```

打开浏览器：

```text
http://127.0.0.1:8765
```

如果没有使用虚拟环境，也可以直接用本机 Anaconda Python：

```bash
cd /Users/liuxinkun/Downloads/projects/AI短剧工作流/drama-agent-system
/Users/liuxinkun/opt/anaconda3/bin/python run_workbench.py --host 127.0.0.1 --port 8765 --workspace .. --outputs outputs
```

如果端口已被占用：

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
kill <PID>
```

再重新执行启动命令。

## 工作流

1. 把材料放入 `资料库/`，或在页面中上传到 `uploads/`。
2. 在 Web 工作站首页选择材料，执行解析。
3. 检查材料详情页、章节阅读器和时间线页，确认材料拆分是否可靠。
4. 进入 `剧本生成`，填写主题和年份范围，勾选可用时间线。
5. 生成剧本后，在 `剧本阅读器` 中查看正文、来源卡片和局部修改对话。

没有配置 `DEEPSEEK_API_KEY` 时，材料基础拆分仍可运行，但章节精读、时间线、剧本生成和对话助手会跳过或报出配置提示。

## Natural Earth 地图 API

启动地图 API：

```bash
cd /Users/liuxinkun/Downloads/projects/AI短剧工作流/drama-agent-system
python run_map_api.py --host 127.0.0.1 --port 8770
```

只下载或校验 Natural Earth 数据，不启动服务：

```bash
python run_map_api.py --download-only
```

常用接口：

```text
GET http://127.0.0.1:8770/health
GET http://127.0.0.1:8770/api/maps/regions
GET http://127.0.0.1:8770/api/maps/render?region=china&title=中国区域底图&cities=1
GET http://127.0.0.1:8770/api/maps/render?bbox=25,10,75,45&title=西亚底图&rivers=1&lakes=1
```

地图 PNG 默认写入：

```text
drama-agent-system/outputs/maps/
```

Natural Earth 只提供现代地理底图；历史疆域、迁徙路线、古城点位需要在这个底图 API 上继续叠加。

## 测试

```bash
cd /Users/liuxinkun/Downloads/projects/AI短剧工作流/drama-agent-system
PYTHONPATH=. python -m pytest
```

如果系统里存在第三方包也叫 `tests`，务必保留 `PYTHONPATH=.`，让测试优先使用当前项目的 `tests/` 包。

## 输出位置

```text
drama-agent-system/outputs/material_splits/<book_id>/
drama-agent-system/outputs/script_generations/<generation_id>/
drama-agent-system/outputs/material_workstation.sqlite3
drama-agent-system/outputs/material_rag.sqlite3
drama-agent-system/outputs/maps/
```

`outputs/material_records.json` 会作为兼容快照继续写出；批量查询和页面展示以 SQLite 为主。

## Git 提交建议

查看当前修改：

```bash
git status --short
git diff
```

只提交文档示例：

```bash
git add README.md
git commit -m "Update project README"
git push origin main
```

运行产物、密钥、数据库和大型材料文件不要提交到仓库。
