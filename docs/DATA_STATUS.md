# 数据源连通性复查（VPN 开启后）

复查时间：2026-11-02 12:50–13:25 UTC  
复查方式：本机 Python HTTPS 请求 + 实际入库 + 页面加载。

## 结论

核心数据全部可用：**CMA 台风、JMA 台风、METAR、SYNOP、NDBC、GIBS 卫星、IMERG 瓦片、Esri 底图**。  
船舶接口可用但当前范围内 **0 条**；卫星反演“点观测”仍缺一个可公开访问的数值产品（详见下文）。

## 逐项结果

| 数据源 | 结果 | 说明 |
|---|---|---|
| CMA 台风网 | ✅ 200 | 当前 `2631 IN-FA`，982 hPa / 28 m/s，路径和风圈正常 |
| JMA RSMC | ✅ 200 | `targetTc.json` / `TC2637/forecast.json` 正常 |
| Aviation Weather METAR | ✅ 200 | 区域 bbox 返回约 222 条最新报 |
| Ogimet SYNOP | ✅ 200 | 14 个 WMO block，30h 回填；当前 655 站、72h 约 1.19 万条，24h 变压可用 |
| NDBC | ✅ 200 | 144 条浮标观测 |
| NASA GIBS Himawari-9 | ✅ 200 | 可见光/红外瓦片正常，已生成 3h 帧 |
| GIBS IMERG 瓦片 | ✅ 200 | 降水瓦片正常，约 4–5h 延迟 |
| Esri Dark Gray 底图 | ✅ 200 | 深色底图正常 |
| SAMOS / COAPS | ✅ 200 | THREDDS 和 `Preliminary_ifile_json.php` 正常；当前域内 0 条船 |
| Windy 站点 | ✅ 200 | `/pois/v2/stations` 5° 网格查询，域内约 896 条，含 69 条船报 |
| NOAA GDP 漂流浮标 | ✅ 200 | `gdp-active.geojson`，域内 70 个活动浮标，提供 SST/SLP |
| NCEI global-marine | ✅ 200 | 目录可访问，但属于归档/延迟数据，未用于实时 |
| NRL TC | ✅ 200 | 页面可访问；JS 资源 403，未作为数据源 |
| JTWC | ⚠️ 403 | 页面拒绝自动请求；不作为主源 |
| ATCF b-deck FTP | ⚠️ TLS 失败 | `UNEXPECTED_EOF_WHILE_READING`；不作为主源 |
| NCEI Blended NRT Winds | ⚠️ 过期 | OPeNDAP 最新时间为 2024-09，不能当 2026 实况 |
| IMERG 数值 OPeNDAP | ⚠️ 401 | 元数据可读，数值子集需要 NASA Earthdata 登录 |
| OSI SAF ASCAT 风 | ⚠️ 无公开数值 | Ifremer THREDDS 公开目录只有 SST/海冰；ASCAT 风需要 EUMETSAT/CMEMS 账号 |

## 对产品的影响

- **站点 24h 指标**：不受影响，SYNOP 回填后约 596 站有 24h 变压。
- **船舶**：Windy 补充站当前提供约 69 条船报（WMO/PWS/MADIS/船舶/浮标），SAMOS 仍作为备用。Windy 是地图 POI 接口，不是官方开放数据 API，使用需遵守其条款；接口变动时自动降级。
- **漂流浮标**：NOAA GDP 活动浮标约 70 个，主要提供 SST/SLP；部分有气温、波高和风向，没有风速时显示 `—`。
- **卫星反演点**：当前只接入卫星云图（面）和 IMERG 降水（面），没有伪造 ASCAT 点观测。若需要真正的卫星风矢量站点，需要：
  - NASA Earthdata 账号（IMERG/CYGNSS/ASCAT 数值），或
  - EUMETSAT/CMEMS 账号（OSI SAF ASCAT L2/L3）。
- **卫星云图**：GIBS 有约 30–60 分钟延迟，页面显示每帧 UTC 时间。

## 当前快照

- 站点：约 1060 个；Ogimet SYNOP 覆盖约 630 站；有 24h 变压约 627 个；GDP 漂流浮标约 70 个。
- 活跃台风：1 个（2631 IN-FA）。
- 卫星帧：约 36 帧（可见光 + 红外），3h 窗口。
- 源状态：全部 `ok`；船舶约 100 条（Windy）。
