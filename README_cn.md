# TraderLens

[English](README.md) · **中文**

**高阶透视交易信息,助力提升交易。** 把券商导出的成交记录,变成一个
自包含的 HTML 复盘报告：资金曲线、日历热力图、按交易模式打分、R-multiple、逐笔下钻,全部联动筛选,纯离线,不用搭服务器。

![TraderLens HTML 透视 — 总览](assets/screenshots/01-overview.png)

*截图来自内置的 [demo 数据](demo/)——50 笔成交。*

### R-multiple——你的优势是真的吗?止损纪律守住了吗?

美元会掩盖两件事:仓位大小和风险纪律。填上你进场时的**计划止损**,
TraderLens 就把 **R**(盈亏 ÷ 计划风险)作为一个维度渗进整个报告——
外加一张带 **−1R 地板**的焦点图。悬停看任意一笔;点击那些**砸穿**止损的
交易,用美元看清失控的代价。可选、逐笔——没填止损就不显示 R,绝不挡路。

![R-multiple 分布](assets/screenshots/r-multiple.png)

<details>
<summary><strong>更多截图</strong> — 资金曲线 · 日历热力图 · 按交易模式打分 · 透视 · 明细表</summary>

### 资金曲线

![资金曲线](assets/screenshots/02-equity-curve.png)

### 日历热力图

![日历热力图](assets/screenshots/03-calendar.png)

### 按交易模式打分

![按交易模式打分](assets/screenshots/04-by-setup.png)

### 透视表(Class × Setup 切片)

![透视表](assets/screenshots/05-pivot.png)

### 明细表(联动筛选)

![明细表](assets/screenshots/06-detail.png)

</details>

---

## 给谁用的?

周末复盘,你大概会问:

- 到底哪些交易模式真在赚钱?
- 一天里,哪个时段是我最赚的?
- 周二那笔亏损,是某一场盘崩了,还是一整周零零碎碎亏出来的?

这些用 Excel 也能算,就是慢。TraderLens 把高阶透视直接呈现在一个
HTML里,浏览器打开就看,离线运行。

如果你的券商能导出成交记录(目前支持 Interactive
Brokers,欢迎接入更多),未来就都能接入。

---

## 先试试 demo(零配置)

**双击 [`demo.html`](demo.html)** 就行。

这是个自包含的 HTML 文件(~440 KB,jQuery 和 PivotTable.js
都内联进去了),用 50 笔成交生成。什么系统、
什么浏览器都能打开,离线可用,不跑任何代码,也不碰你机器上
别的文件。

看着合胃口,就往下翻到 [在自己的数据上跑](#在自己的数据上跑)。

这份 bundle 的更多细节(HTML 是用哪份 SQLite + CSV 生成的、
数据怎么匿名化的、怎么在本地重跑这条管线):
[demo/README.md](demo/README.md)。

---

## 你能得到什么

五个视图,全都渲染进同一个自包含 HTML 文件
(~400 KB,浏览器打开,离线可用):

- **KPI 区块** — 胜率、盈亏比、回撤,平均 / 最好 / 最差单笔。
- **资金曲线** — 累计盈亏,带日期刻度,下面叠一条回撤带。
- **日历热力图** — 每天的盈亏按日历铺开;点某一天就能钻进去看。
- **按交易模式打分** — 你标的每个策略,实际跑出来什么成绩。
- **透视 + 明细表** — 拖拽式透视(PivotTable.js),配一张能排序、
  能筛的成交清单,两边都跟着同一套筛选条件联动。

> 在线版本就是项目根目录那个 [`demo.html`](demo.html)——双击即可。

---

## 在自己的数据上跑

TraderLens v1 自带一个 **Interactive Brokers** 的 adapter,走的是
IBKR 只读的 Flex Web Service。

**一次性配置**(~5 分钟):

1. 在 IBKR Client Portal → Settings → Flex Web Service 里拿一个
   Flex token。你会建一个 *Activity* query,再(可选)建一个
   *Trade Confirmation* query。
2. 把 `.env.example` 复制成 `.env`,填上:
   ```
   IBKR_FLEX_TOKEN=...
   IBKR_FLEX_QUERY_ID=...
   ```
   `.env` 已经在 gitignore 里——绝不会被提交。

**日常使用** — 每个平台一个脚本,都是一条龙:抓取 + 归档 + 重写 CSV:

- **Windows** — 双击 [`scripts\run_ib_sync.bat`](scripts/run_ib_sync.bat)
- **macOS / Linux** — `bash scripts/run_ib_sync.sh`(已带可执行权限;或 `./scripts/run_ib_sync.sh`)

**让它自己跑**(基本就能撒手不管了):

- **Windows** — 注册一个 Windows 任务计划程序条目:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\register_ib_sync_task.ps1
  ```
- **macOS** — 装一个 launchd agent:
  ```bash
  bash scripts/install-launchd-task.sh
  ```
- **Linux** — `cron` / `systemd` timer / `anacron` 随你挑。按你
  喜欢的节奏跑 `scripts/run_ib_sync.sh --no-delay --mode auto` 就行。
  (每 4 小时跑一次的 cron 单行:
  `0 */4 * * * /path/to/scripts/run_ib_sync.sh --no-delay --mode auto`。)

想做标注 / 重新打分时,刷新一下 HTML 透视:
**Windows** — `scripts\review.bat`;**macOS / Linux** — `bash scripts/review.sh`。

完整操作手册(日志、退出码、排查、所有命令):
[docs/guides/OPERATIONS.md](docs/guides/OPERATIONS.md)。

> **Flex 限流,这段务必读一遍。** Interactive Brokers 强制 Flex
> 调用之间至少隔 10 分钟;滥用可能让你的 IP 被**永久封禁**——
> 连所有 IBKR API 都用不了。TraderLens 在代码层面就把这条卡死了
> (10 分钟闸门 + 撞限流后进 30 分钟 penalty box,绝不盲目重试)。
> 别去禁用这个闸门——见 [ADR-002](docs/decisions/002-flex-rate-limit-policy.md)。

---

## 隐私与数据所有权

**所有东西都留在你自己机器上。** TraderLens 是 local-first 的——
没有作者运营的后端、没有云服务、没有遥测、没有埋点、没有崩溃
上报。作者根本看不到你的成交、账号、Flex token、姓名、IP,
什么都看不到。压根不存在一个能接收你数据的"我们"。

什么东西存在哪(全在你机器上,全部 gitignore):

- **券商凭据** — `.env`(`IBKR_FLEX_TOKEN`、`IBKR_FLEX_QUERY_ID`)。
- **成交数据** — `data/trades.sqlite`、`data/annotations.csv`、
  `data/exports/*.csv`、`data/state.json`。
- **HTML 报告** — `reports/pivot_latest.html`。
- **日志** — `logs/ib_sync_*.log`。

TraderLens 一共会发起的网络连接,就这两种:

1. **你的机器 → Interactive Brokers** Flex Web Service
   (`https://*.interactivebrokers.com/...`),用*你自己的* Flex
   token 认证,抓的是*你自己*账户的成交。只读。
2. `pip install -r requirements.txt` — 只在装的时候连,而且只连 PyPI。

就这些,没有第 3 条。万一以后某个功能要连新的网络地址(比如一个
可选的云端标注同步),会写在这里、默认关掉、要你自己手动开。

HTML 透视里内联的第三方 JavaScript(jQuery、jQuery UI、
PivotTable.js)都是 **本地 vendored** 在 `assets/vendor/` 下——
报告不依赖任何 CDN,完全离线可用。

---

## 路线图

- **透视 Tier-2** — 把当前筛选结果导出成 CSV、每次同步后自动
  重生、更多交叉维度。
- **当天捕获** — 收盘后用 Trade Confirmation Flex query 抓当天
  成交(和现有的 T+1 Activity feed 并行)。
- **更多券商 adapter。** TraderLens 设计上就是 broker-agnostic
  的。IBKR 只是第一个 adapter,不是唯一目标——标注层、透视、
  导出 schema 都跟具体券商无关,只有抓取 + 解析这一层是按券商
  写的。其他券商的 adapter(`coinbase_sync`、`td_sync`、
  `binance_sync`…)都是一等公民,非常欢迎来贡献。

---

## 状态与限制

这是个**用来做个人记录的 alpha 阶段软件**。不构成投资建议。
不用于自动交易。在你把这里的任何数字当真之前,永远先拿券商
自己的对账单核一遍。

**用了 TraderLens,就表示你接受 [DISCLAIMER.md](DISCLAIMER.md) 里的条款**
——不对数据完整性做担保、不对亏损负责,你也有责任遵守你券商
的条款和你当地的法律。

v1 是在一个美股模拟账户上,针对期货(NQ / MNQ / ES / MES)和
股票测过的。别的品种(期权、外汇、加密、非美市场)可能算错——
parser 的字段假设是照着那个账户观察到的情况调出来的。

---

## 架构(好奇就扫一眼)

<details>
<summary><strong>60 秒架构总览</strong></summary>

```
 券商(目前是 Interactive Brokers)
        │
        │  Flex Web Service — 只读,两步轮询
        ▼
 抓取器(Python,stdlib + requests)
        │
        ▼
 SQLite 归档 — 每一笔成交,幂等
        │
        ├─▶ CSV 导出 — schema 稳定,机器可读
        │
        └─▶ HTML 透视 — 一个自包含文件供复盘
```

分三层:

- **事实(Fact)** — 不可变,券商给定。`data/trades.sqlite`,只由
  抓取器用 `INSERT OR IGNORE` 写。重跑很安全。
- **标注(Annotation)** — 归你自己。`data/annotations.csv`,你在
  Excel 里填 `setup_tag` / `score` / `notes`;以开仓腿的 trade ID
  为键,所以重抓、重配对之后,标注都还在。
- **派生(Derived)** — 随时能重算。CSV 导出和 HTML 透视都是
  *事实 + 标注* 的纯函数;删了重新生成就行。

技术栈:Python 3.10+、`requests`(唯一的运行时依赖)、解析用
stdlib `xml.etree`、存储用 SQLite。HTML 透视内联了 jQuery +
PivotTable.js(MIT),所以报告能离线用。

</details>

---

## License

TraderLens 按 [AGPL-3.0](LICENSE) 授权。作者作为唯一贡献者保留
完整版权;补齐 CLA 之后,未来做 dual-licensing 仍是个选项。
网络使用互惠条款(AGPL §13)意味着:谁拿它做 SaaS 转托管,
就得把整套技术栈也开源。背景见
[ADR-003](docs/decisions/003-license-agpl-3.0.md)。

vendored 在 `assets/vendor/` 下的第三方资产(jQuery、jQuery UI、
PivotTable.js)是 MIT 授权;完整署名见
[assets/vendor/README.md](assets/vendor/README.md)。

---

## 另见

- [DISCLAIMER.md](DISCLAIMER.md) — 非投资建议,使用风险自负。
- [CONTRIBUTING.md](CONTRIBUTING.md) — 怎么提 issue、分支与提交
  约定、code review 流程。
- [CHANGELOG.md](CHANGELOG.md) — 发布说明。
- [docs/INDEX.md](docs/INDEX.md) — 完整文档索引(操作、ADR、
  study spike)。
