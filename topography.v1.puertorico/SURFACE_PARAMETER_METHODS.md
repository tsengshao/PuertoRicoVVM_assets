# 地形與地表參數計算方法

這份文件說明 `build_puerto_rico_topography.py` 如何從 USGS 3DEP DEM 與
ESA WorldCover 產生 VVM、VVMex 使用的地形及地表參數。

## 整體流程

```text
3DEP DEM + WorldCover
        ↓
共同投影與 master grid
        ↓
各 WorldCover 類別面積比例、平均高度、平均坡度
        ↓
VVM / VVMex land type、soil type、slope type 與連續地表參數
        ↓
垂直離散 topo、cave correction、三維 mask
```

所有目標解析度都直接從 `Puerto_Rico_surface_master.nc` 聚合，不會先從
500 m 算出 1 km，再從 1 km 算出 2 km。

## WorldCover 面積比例

每個目標網格內分別計算 11 個 WorldCover 類別所占的 master cells 比例：

```text
fraction(c) = 類別 c 的 master cell 數量 / 目標格內全部 master cell 數量
```

輸出包含 tree、shrub、grass、crop、urban、bare、snow、water、wetland、
mangrove 與 moss fraction。全部 fraction 的總和應為 1。

## 海陸判斷

```text
water_fraction >= water_fraction_threshold  → 海洋
water_fraction <  water_fraction_threshold  → 陸地
```

目前通常使用 `water_fraction_threshold=0.5`。海洋格的高度、坡度及 topo
設為 0。`land_fraction = 1 - water_fraction`。

若啟用 `main_island_only`，程式先在 master grid 使用四鄰接 connected
components 保留最大的陸地區塊；其他島嶼在聚合前改成水域。每個目標解析度
完成 water-fraction 離散化後，程式會再做一次四鄰接檢查，只保留最大的陸地
component。這會移除離散化新產生的不連通陸塊；被移除格點的高度、坡度及
land fraction 設為 0，WorldCover fractions 則設為 100% water。移除的格點與
component 數會記錄在 QA JSON 的 `post_regrid_main_island_cleanup`。

## Land type

Land type 是類別參數，因此不會直接平均類別 ID。計算方法是：

1. 計算每個 WorldCover 類別在目標格內的 fraction。
2. 目標格若已判為陸地，類別投票排除 water fraction。
3. 將其餘 WorldCover 類別映射成 VVM 或 VVMex 類別。
4. 映射到相同 model class 的 fractions 相加。
5. 選擇 fraction 最大的 model class。
6. 海洋格強制使用模型的水域類別。

| WorldCover ID | 地表類別 | VVM `lu`（USGS） | VVMex `vegtype`（Modified IGBP） |
|---:|---|---:|---:|
| 10 | Tree cover | 13 | 2 |
| 20 | Shrubland | 8 | 7 |
| 30 | Grassland | 7 | 10 |
| 40 | Cropland | 4 | 12 |
| 50 | Built-up | 1 | 13 |
| 60 | Bare/sparse vegetation | 19 | 16 |
| 70 | Snow and ice | 24 | 15 |
| 80 | Permanent water | 16 | 17 |
| 90 | Herbaceous wetland | 17 | 11 |
| 95 | Mangroves | 18 | 11 |
| 100 | Moss and lichen | 20 | 19 |

## Soil type

目前沒有獨立土壤資料，因此 soil type 是由 WorldCover 推估的原型設定。
計算方式與 land type 相同：先映射、合併 fraction，再選 dominant soil
class。

| WorldCover | Soil ID | 目前代表意義 |
|---|---:|---|
| Tree、shrub、grass、crop、built-up、moss | 6 | Loam |
| Bare/sparse vegetation | 3 | Sandy loam |
| Snow and ice | 16 | Land ice |
| Permanent water | 14 | Water |
| Wetland、mangrove | 13 | Organic soil |

海洋格會強制設為 `soil=14` 或 `soiltype=14`。若要製作正式的土壤初始場，
應改用獨立 soil dataset。

## Albedo、GVF 與 LAI

這些是連續參數，使用 WorldCover fractions 加權平均：

```text
parameter_target = Σ fraction(c) × representative_value(c)
```

例如一格包含 60% tree 與 40% grass：

```text
GVF = 0.6 × 95 + 0.4 × 80 = 89%
```

目前使用的代表值如下：

| WorldCover | Albedo (%) | GVF (%) | LAI (m²/m²) |
|---|---:|---:|---:|
| Tree | 12 | 95 | 6.48 |
| Shrub | 25 | 70 | 2.00 |
| Grass | 19 | 80 | 4.00 |
| Crop | 19 | 80 | 4.00 |
| Built-up | 15 | 10 | 1.00 |
| Bare/sparse | 38 | 1 | 0.10 |
| Snow/ice | 70 | 0 | 0.01 |
| Water | 8 | 0 | 0.00 |
| Wetland | 14 | 60 | 4.00 |
| Mangrove | 12 | 95 | 6.48 |
| Moss/lichen | 16 | 60 | 2.00 |

VVM 與 VVMex 使用相同的加權結果。因為目前沒有季節植被資料：

```text
shdmax = GVF
shdmin = GVF
```

因此 `shdmax`、`shdmin` 目前不是季節最大值與最小值，只是 GVF 的替代值。

## 高度

DEM 先投影到共同的 master grid。無效值、負高度與水域高度設為 0。目標格
高度是該格內全部 master cells 的算術平均：

```text
height_target = mean(height_master)
```

因此沿岸陸地格的平均包含水域的 0 m 高度。若目標格最後判為海洋，平均高度
再強制設為 0。

## Slope

坡度先在 master DEM 上以水平梯度計算：

```text
slope_percent = 100 × sqrt((∂z/∂x)² + (∂z/∂y)²)
```

`surface` 檔案中的 `slope_percent` 是目標格內 master slope 的平均值。
VVM 和 VVMex 的整數坡度類別則直接使用 master land cells 的坡度分布。

VVM 將坡度分成三個 regime：

```text
low:    slope < 8%
medium: 8% <= slope < 30%
high:   slope >= 30%
```

某個 regime 占該格陸地面積至少 5% 就視為存在，再組合成：

| VVM `slope` | 意義 |
|---:|---|
| 1 | low |
| 2 | medium |
| 3 | high |
| 4 | low + medium |
| 5 | low + high |
| 6 | medium + high |
| 7 | low + medium + high |
| 0 | 海洋 |

若三組都未達 5%，使用 fraction 最大的 regime。

VVMex 使用較細的坡度 bins，選擇陸地面積 fraction 最大的一組：

| VVMex `slopetype` | Slope (%) |
|---:|---|
| 1 | 0–8 |
| 2 | 8–16 |
| 3 | 16–30 |
| 4 | 30–45 |
| 5 | 45–60 |
| 6 | 60–90 |
| 7 | 90–120 |
| 8 | 120–150 |
| 9 | ≥150 |
| 0 | 海洋 |

## Topography 與 mask

目標格平均高度先對照設定的 vertical grid，選取最近的垂直高度索引，接著轉成
模型的 1 起始編號並讓所有陸地再高一格。製作 VVM/VVMex topo 前，程式會以
四鄰接方式由 domain 邊界辨識外海；封閉湖泊及透過一至兩格窄水道連到海岸的
內陸河段也會加入 model terrain，但不改變 `surface_*.nc` 的海陸分類：

```text
exterior ocean topo = 0
minimum land/inland-water topo = 2
```

離散後對陸地與上述島內水格執行 single-grid cave correction。若中心格的東、
西、南、北四格都比中心高，中心 topo 提高到四個鄰格的最小值，重複到穩定或
達到設定的 iteration 上限。湖／河格的初始值為 2；若它構成 single-grid cave，
最終值可能高於 2。寫檔前會強制驗證 model terrain 全部 `topo >= 2`、外海全部
`topo = 0`，且沒有四鄰接 cave；驗證失敗時停止，不寫出有問題的模型檔。

VVM 和 VVMex 的二維 `topo` 使用相同編號。VVMex 若設定
`vvmex_drop_bottom_level=true`，會省略 VVM 最下面的地板層，因此只在建立三維
`mask` 時少計一個 blocked level；二維 topo 不變。

## VVMex 固定初始值

目前缺少獨立的地表溫度與土壤含水量資料，所以使用固定初始值：

```text
Tg = 300 K
sm1–sm4 = 0.3054033 m³/m³
sl1–sl4 = 0.3054033 m³/m³
```

這些值是初始化用的預設值，不是觀測或 Puerto Rico climatology。

## 資料來源與限制

- 地形高度與 slope：USGS 3DEP DEM。
- 海陸與 land-cover fractions：ESA WorldCover 2021 v200。
- Land type mapping：VVM USGS 與 VVMex Modified IGBP/MODIS Noah taxonomy。
- Albedo、GVF、LAI：目前程式內的 land-cover representative values。
- Soil、`shdmax/shdmin`、`Tg` 與 soil moisture 是原型或固定設定，正式實驗前應
  視需求換成獨立資料集。
