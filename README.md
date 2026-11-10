# Alogs

中文 AI 资讯、作者观点与 GitHub 工具发现。访客直接打开网页，无需安装或运行本地服务。

网站：https://maxine0917.github.io/Alogs/

## 三个模块

- **AI-HOT**：AIHOT 的资讯、热门话题与正式日报，保留来源链接和真实日期。
- **Follow Builders**：作者原帖、按作者与日期整理的中文提要，以及播客内容。
- **GitHub 发现**：独立工具与真实 Skill 文件；按原始来源仓库 Star 择优，每天最多 5 个项目和 5 个 Skills，超额顺延。

三个模块共用日期；支持筛选、搜索、收藏、展开原帖、复制安装话术和浅色／深色模式。收藏、关注主题和外观偏好保存在各自浏览器，不会共享给其他访客。

## 自动更新

GitHub Actions 每 5 分钟安排一次云端检查，GitHub Pages 发布采集结果。浏览器打开期间每分钟检查一次版本；点击“刷新”立即读取已发布的最新结果。检查频率不等于延迟保证：上游可能没有新内容，Actions 可能排队，采集与 Pages 发布也需要时间。

资讯、热门话题、日报与作者信息流按这个频率检查。GitHub 来源每天北京时间 08:00 之后第一次运行时同步；若定时运行延迟，则顺延执行。Star 或安装量变动不重复推荐，内容新增与更新共用每日名额。

中文作者提要只在原文变化后调用模型，按内容指纹缓存。访客阅读、刷新与收藏不触发模型调用。

状态数据库通过 Actions artifacts 恢复和保存，包含去重游标、待发布队列、每日推荐账本和摘要缓存。只保留最近 3 份状态及 2 份网站发布产物。仓库中的 `seed/*.json.gz` 是首次上线的公开数据快照，不含密钥；后续恢复失败会停止任务，避免静默重置名额。

GitHub 对长期无仓库活动的公开仓库可能停用定时任务。可在 Actions 中手动重新启用；不要将本方案当作有严格可用性保证的实时后端。

## 自行部署

1. 创建自己的公开仓库并上传源码。
2. 在仓库 Settings → Pages 选择 **GitHub Actions**。
3. 在 Actions secrets 配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`；可选 `LLM_EXTRA_JSON`、`LLM_REASONING_TOKENS`。模型请求使用 OpenAI 兼容接口。
4. 运行 **Sync and publish Alogs**。首次创建状态，随后自动恢复。

模型密钥只进入 Actions 环境，不进入网页、数据产物或 Git 提交。若不需要模型，修改 workflow 的 `MODEL_CALLS_ENABLED` 为 `false`；原帖仍可阅读。

同步日志与状态产物在公开仓库可见，仅保存公开上游数据和中文整理结果。文章和原帖的版权属于各原作者；本页提供摘要、引用与来源链接，不改变来源内容。

## 本地预览与验证

需要 Python 3.12，GitHub 采集另需要已认证的 `gh`。

```sh
python -m unittest discover -s tests -v
python scripts/state.py seed
python -c "import sys; sys.path.insert(0, 'scripts'); from cloud_sync import publish_manifest; publish_manifest()"
python -m http.server 8080 --directory site
```

打开 http://localhost:8080 。这是云端页面的静态预览，刷新读取本地公开数据文件。

## 目录

`site/` 是公开页面；`scripts/` 是云端采集与发布逻辑；`sources.json` 是来源及收录政策；`seed/` 是首次上线快照；`.github/workflows/publish.yml` 是定时任务。

字体 Instrument Sans 的许可证在 `site/_shared/fonts/InstrumentSans-OFL.txt`。
