# 抄表簿

骨架：抄表读数装载 → 按表类型分桶对齐 → 差额处置 → 分类型封印。

- 前端 http://127.0.0.1:6010（勿用 6000，浏览器会拦）
- 接口 http://127.0.0.1:11000

## 封印架构（全局唯一口径）

本系统只贯彻**按 `meter_type` 分桶对齐、分类型封印**这一种架构，不做混类型整账期一把封，
也不做楼栋分区封印换皮。

- 同一账期下水表（water）、电表（electric）各持有一条 `period_gates` 写闸与各自的
  `reconcile_batches` 类型批；批与差额快照均带 `meter_type`，永不混桶。
- 对齐 `POST /api/align` 与封印 `POST /api/seal` **必须带 `meter_type`**，缺参数返回 422，
  非法类型返回 400；不存在“未带类型却宣称分类型封”的入口。
- 封印只关闭 `(period_id, meter_type)` 这一条写闸：封水表后电表同账期仍可写实抄/估抄，
  反之亦然；两类都封后两类均拒写。
- 类型批不得跨类型封印（批的 `meter_type` 必须与请求一致），已封印类型批无法被改类型撬动。
- 总览水/电写闸计数直接取自 `period_gates`，与写入拦截同表同源。
- 封印、对齐、读数写入、差额处置均以 `BEGIN IMMEDIATE` 事务串行化，并发分类型封印互不
  覆盖、不留半截类型批，写入与封印之间无 check-then-act 竞态。

## 主要接口

| 接口 | 说明 |
| --- | --- |
| `POST /api/align` | body 必含 `period_id`、`meter_type`，只对齐该类型 |
| `POST /api/seal` | body 必含 `period_id`、`meter_type`，`batch_id` 可空（取该类型最新 open 批） |
| `GET /api/batches?period_id=&meter_type=` | 类型批列表，可按类型过滤 |
| `GET /api/variances?batch_id=&period_id=&meter_type=` | 差额快照，带 `meter_type` |
| `GET /api/overview` | 水/电写闸计数、当前账期各类型闸门、类型化未决差额 |
| `GET /api/periods` | 每账期附水/电各自闸门状态与 `sealed_types` |
| `GET/POST /api/estimates`、`/api/actuals` | 支持 `meter_type` 过滤；写入按该表所属类型的闸门拦截 |

## 启动

```powershell
cd backend
C:\Users\18435\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 11000 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 6010
```

## 测试

```bash
cd backend
python -m pytest tests/ -q
```

- `tests/test_arch_typed.py`：分类型架构全链路核对（含并发封印、写入/封印竞态）。
- `tests/test_arch_mixed_rejected.py`：混封架构反向核对，证明无类型封印被拒、封一类不波及另一类、不混桶、不是楼栋分区。
- `tests/test_end_to_end_smoke.py`：完整 ASGI 栈端到端冒烟。
