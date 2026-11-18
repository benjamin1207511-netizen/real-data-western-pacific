# BAMC 风眼 · TC WATCH 设计方案 v0.2

> 西北太平洋热带气旋实况监测平台  
> 范围：**5°N–40°N，100°E–160°E**  
> 目标：一屏看清「台风在哪、强度多少、周围站点风压温湿雨如何、24 小时变了多少、卫星云图长什么样」。

> **界面精简（2026-11-18）**
> - 已移除：活跃热带气旋面板、CSV 导出、数据质量面板、IMERG 降水图层、最佳路径/台风路径图层、所有数据来源标识。
> - 保留：站点/船舶/漂流浮标、卫星云图与时间轴、站点排行、站点 72h 曲线。
>
> **范围修订（以此为准）**
> - 不做气压/风场模式背景（GFS/ECMWF 等压线或风场已移除）。
> - 不按“第一版/第二版/第三版”分期；持续迭代。
> - 已实装：METAR/SYNOP 去重、24h 变压/变温、站点 72h 曲线、卫星 3h 动画、移动端、自动重试与监控。

---

## 1. 产品定位

不是单纯的台风路径图，而是把**热带气旋实况**和**地面/海上站点实况**放在同一张地图上的监测台。

核心原则：

1. **先看结论**：首页直接给出活跃气旋、24h 降压榜、24h 变温榜、大风/强降水榜。
2. **观测和模式分开**：不做模式气压/风场背景；地图上的气压和风只来自站点观测与官方风圈。
3. **缺测就说缺测**：24h 指标缺数据时显示 `—`，不用模式值补。
4. **每个数字可追溯**：站点详情给出数据来源、延迟、质量标记。

---

## 2. 页面与交互

### 2.1 首页 `/`

```text
┌────────────────────────────────────────────────────────────────────┐
│ BAMC 风眼 TC WATCH   UTC 13:20  本地 21:20   数据 ●   刷新  全屏  │
├────────────────────────────────────────────────────────────────────┤
│ BAMC · TROPICAL CYCLONE WATCH                                      │
│ 风起西太，云图见风眼                                                │
├───────────────┬──────────────────────────────┬─────────────────────┤
│ 活跃气旋       │                              │ 站点排行             │
│ ● CHAMPAKA    │   卫星云图 + 台风路径/风圈     │ 24h 降压 / 变温      │
│   975 hPa     │   站点风羽 / 变压色斑          │ 大风 / 强降水        │
│   33 m/s      │                              │                     │
│               │                              │ 数据质量             │
│ 图层控制       │                              │                     │
├───────────────┴──────────────────────────────┴─────────────────────┤
│ 时间轴：卫星 3h 动画 / 播放 / 拖动 / 速度                          │
└────────────────────────────────────────────────────────────────────┘
```

- 视觉参考 **BAMC ECMWF 预报**：白色页面、白色卡片、蓝色主色（`#0066cc` / `#1e7bb8`）、细边框 + 轻阴影、Inter/系统字体；主地图作为深色内容视窗保留对比度。
- 桌面：左栏 264px（气旋+图层+图例），中间地图，右栏 296px（排行+质量）。
- 移动：地图全屏，底部抽屉 + 四个入口（气旋 / 排行 / 图层 / 质量）。
- 点击站点 → 右侧详情抽屉；点击台风 → 选中并聚焦。

### 2.2 气旋详情

- 当前中心、中心气压、最大风速、移动方向/速度。
- 24h 中心气压变化（不是站点变压）。
- 34/50/64 kt 风圈（当前时刻）。
- 实况路径、官方预报虚线、预报点。
- 卫星云图、附近站点（后续可加）。

### 2.3 站点详情

- 六个核心数字：MSLP/QNH、24h 变压、气温、24h 变温、风/阵风、24h 降水。
- 72h 曲线：气压、气温/湿度、风速/阵风、降水。
- 数据来源徽章、质量标记、观测时间/延迟、距离台风距离。

### 2.4 站点排行

- 24h 降压、24h 变温、大风、24h 强降水。
- 点击行定位并打开站点详情。
- CSV 导出全部站点当前实况。

### 2.5 数据质量面板

- 每个源：状态、最近成功、延迟、条数、错误信息。
- 自动重试；失败保留上次成功数据。
- 手动“立即刷新”按钮。

---

## 3. 地图图层

| 图层 | 默认 | 说明 |
|---|---|---|
| 卫星云图 | 开 | Himawari-9 B03 可见光 / B13 红外，3h 动画 |
| 台风路径 | 开 | 实况实线、预报虚线、当前中心 |
| 风圈 | 开 | 当前时刻 34/50/64 kt 四象限 |
| 站点风羽 | 开 | zoom≥5 显示，按风速着色 |
| 站点色斑 | 开 | 颜色=24h 变压，大小=风速 |
| IMERG 降水 | 关 | 30 分钟降水率，半透明 |

**明确不做**：海平面气压等压线、10m 风场、GFS/ECMWF 模式背景、雷达拼图。

底图：Esri Dark Gray Canvas + 本地 Natural Earth 海岸线；断网仍可看台风位置。

---

## 4. 指标口径

### 4.1 站点 24h 变压

\[
\Delta P_{24h} = P(t) - P(t-24h)
\]

- 负值 = 降压。
- 两端观测在目标时刻 ±45 分钟内；不满足返回 `null`。
- 同一来源序列计算，不混合 METAR QNH 和 SYNOP MSLP。
- 同时展示 3h 变压。

### 4.2 站点 24h 变温

\[
\Delta T_{24h} = T(t) - T(t-24h)
\]

- 同一来源、同一时间窗；超过 ±20°C 视为异常并丢弃。

### 4.3 降水

- 1h / 3h / 24h。
- 优先 SYNOP `6RRR1` / `7RRRR`；`9999` 等特殊值过滤。
- 所有累计值标注起止时间。

### 4.4 风

- 10 分钟平均风速、阵风、风向。
- m/s 为主，kt / km/h / 蒲福风级为辅。
- 风羽按 5 kt 半羽、10 kt 整羽、50 kt 三角旗。

### 4.5 台风 24h 变化

\[
\Delta P_{TC,24h} = P_{center}(t) - P_{center}(t-24h)
\]

- 与站点变压分开显示，避免混淆。
- 路径点 3/6 小时间隔，24h 值用线性插值并标注。

---

## 5. 数据源

| 数据 | 主源 | 变量 | 频率/延迟 | 角色 |
|---|---|---|---|---|
| 台风路径/强度/风圈/预报 | [CMA 台风网](https://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default) | 位置、气压、风速、风圈、预报 | 约 3–10 分钟 | 主源 |
| 台风位置/概率圈 | [JMA RSMC](https://www.jma.go.jp/bosai/typhoon/data/targetTc.json) | 位置、概率圈 | 约 10 分钟 | 备用/校验 |
| 机场站 | [Aviation Weather METAR](https://aviationweather.gov/api/data/metar?bbox=5,100,40,160&format=json&hours=3) | 风、温度、露点、QNH | 20–60 分钟 | 风/温/湿主力 |
| 地面站 | [Ogimet SYNOP](https://www.ogimet.com/cgi-bin/getsynop) | MSLP、3h 气压倾向、降水、气温、风 | 1–3 小时 | 气压/降水主力 |
| 浮标 | [NDBC](https://www.ndbc.noaa.gov/) | 风、气压、气温、水温 | 10 分钟 | 海洋补充 |
| Windy 补充站 | [Windy](https://www.windy.com/) | WMO/PWS/MADIS/船舶/浮标 | 约 15 分钟 | 补海洋和城市站 |
| GDP 漂流浮标 | [NOAA GDP](https://out-gdpsio.ucsd.edu/apps/projects/noaa/global-drifter-program.html) | SST、SLP、部分气温/波高/风向 | 约 10 分钟 | 海洋补充 |
| 船舶/平台 | [SAMOS/COAPS](https://samos.coaps.fsu.edu/html/webservices.php) | 风、气压、气温、湿度 | 近实时 | 海洋补充 |
| 卫星云图 | [NASA GIBS Himawari-9](https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml) | B03 可见光、B13 红外 | 10 分钟帧，30–60 分钟延迟 | 云图 |
| 降水 | [NASA GIBS IMERG](https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml) | 30 分钟降水率 | 30 分钟帧，4–5 小时延迟 | 空间降水 |
| 历史 | [IBTrACS](https://www.ncei.noaa.gov/products/international-best-track-archive) | 最佳路径 | 每日/不定期 | 后续回放 |

**数据边界**：

- 海洋固定站稀疏，不伪造海上站点。
- 卫星反演点需要 ASCAT/IMERG 数值账号；当前只接卫星云图和 IMERG 面产品。
- QNH 与 MSLP 口径不同，详情页标注。
- 卫星云图时间戳必须显示，不能假装“现在”。

---

## 6. 架构

```text
CMA / JMA / METAR / SYNOP / NDBC / SAMOS / GIBS
                    │
                    ▼
        FastAPI ingest + 后台调度线程
                    │
                    ▼
        SQLite WAL
        stations / observations / station_current
        storms / track_points / satellite_frames / source_status
                    │
                    ▼
        FastAPI REST
                    │
                    ▼
        Leaflet + ECharts + 原生 JS（无前端构建）
```

- 后端：Python、FastAPI、SQLite WAL、requests、Pillow、netCDF4。
- 前端：Leaflet 1.9、ECharts 5，本地静态资源。
- 卫星：GIBS WMTS → Pillow 拼接 100–160E/5–40N → PNG 帧。
- 底图：Esri Dark Gray Canvas；本地海岸线回退。

### 数据表

```text
stations(id, wmo_id, icao_id, name, lat, lon, type, source)
station_aliases(source, source_id, station_id)
observations(station_id, obs_time, source, wind/pressure/temp/rh/rain, qc_flags, raw_json)
station_current(station_id, obs_time, dp_24h, dt_24h, wind, rain, quality)
storms(id, agency, number, name, lat, lon, pressure, wind, observed_at)
track_points(storm_id, agency, valid_time, lat, lon, pressure, wind, wind_radii_json, forecast)
satellite_frames(channel, frame_time, path, bounds, width, height)
source_status(source, status, last_success, latency_ms, item_count, message)
```

### API

```text
GET  /api/overview
GET  /api/storms
GET  /api/storms/{id}
GET  /api/stations?limit=2200&storm=...
GET  /api/stations/{id}?hours=72
GET  /api/rankings?metric=dp24|dt24|wind|rain
GET  /api/satellite/frames?channel=ir|visible&hours=3
GET  /api/imerg
GET  /api/status
GET  /api/export/stations.csv
POST /api/refresh
GET  /api/health
```

---

## 7. 质量控制

1. 范围检查：气压 850–1100 hPa、气温 −50–55°C、湿度 0–100%、风速 0–90 m/s。
2. 时间一致性：同一站同一变量 10 分钟内重复记录保留最新。
3. 24h 变化异常过滤：气压 ±80 hPa、气温 ±20°C。
4. 降水特殊值过滤：`999.9` 等。
5. 来源优先级按变量而不是按站点：气压/降水优先 SYNOP，风/温优先 METAR，海洋用 NDBC/SAMOS。
6. 每个数字可追溯来源和原始报文。

---

## 8. 当前状态与后续

**已交付**

- 浅色 BAMC 业务界面 + 深色地图视窗；台风路径/风圈。
- METAR + SYNOP 接入、去重、单位统一。
- 24h 变压/变温排行、站点 72h 曲线。
- Himawari-9 可见光/红外 3h 动画。
- IMERG 降水、CSV 导出、移动端、数据质量面板、自动重试/监控。

**受限项**

- 船舶：Windy 补充站当前提供约 69 条船报；SAMOS 作为备用。
- 漂流浮标：GDP 约 70 个活动浮标，主要提供 SST/SLP，部分有气温/波高/风向。
- 卫星反演点：需要 ASCAT/IMERG 数值账号，暂未伪造点观测。
- JTWC：自动请求 403；ATCF TLS 失败，不作为主源。

**后续**

- 有 Earthdata/EUMETSAT 账号后，接 ASCAT/CYGNSS 风矢量或 IMERG 数值，生成卫星反演站点。
- IBTrACS 历史回放。
- 更多国家站点（CWA/PAGASA 等）。

---

## 9. 署名与免责声明

数据：CMA、JMA、NOAA/NWS、NASA GIBS/IMERG、NDBC、SAMOS/COAPS、IBTrACS。底图：Esri。

本项目为科研/教学/信息展示用途，**不是官方预警产品**。台风路径、强度、风圈以官方发布为准；站点观测可能存在延迟、缺测或错误；卫星云图和 IMERG 为派生可视化，不代表官方定强。
