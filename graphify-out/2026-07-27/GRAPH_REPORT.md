# Graph Report - .  (2026-07-16)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 188 nodes · 365 edges · 9 communities
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 44 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `fcc4e662`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- GPIOHandler
- MeterConfig
- MeterReading
- Database
- settings.py
- Application
- RegisterDef
- ModbusPoller

## God Nodes (most connected - your core abstractions)
1. `GPIOHandler` - 27 edges
2. `ModbusPoller` - 21 edges
3. `RegisterDef` - 18 edges
4. `MeterConfig` - 17 edges
5. `Application` - 17 edges
6. `MeterReading` - 17 edges
7. `Database` - 16 edges
8. `ModbusBackend` - 15 edges
9. `RedisPublisher` - 15 edges
10. `RingBuffer` - 13 edges

## Surprising Connections (you probably didn't know these)
- `GPIOHandler` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/gpio_handler.py → energy-collector/config/settings.py
- `State` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/gpio_handler.py → energy-collector/config/settings.py
- `ModbusPoller` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/modbus_client.py → energy-collector/config/settings.py
- `MockModbusBackend` --uses--> `RegisterDef`  [INFERRED]
  energy-collector/core/hardware/modbus_backend.py → energy-collector/config/settings.py
- `ModbusBackend` --uses--> `RegisterDef`  [INFERRED]
  energy-collector/core/hardware/modbus_backend.py → energy-collector/config/settings.py

## Import Cycles
- None detected.

## Communities (9 total, 0 thin omitted)

### Community 0 - "GPIOHandler"
Cohesion: 0.08
Nodes (22): CycleCloseHook, CycleOpenHook, cleanup_gpio(), _gpio_setup_hints(), GPIOHandler, init_gpio(), _new_id(), _now() (+14 more)

### Community 1 - "MeterConfig"
Cohesion: 0.09
Nodes (17): ABC, MeterConfig, Konfigurasi satu meter dari `meters.yaml`., Hardware abstraction layer — Modbus backend (asli / mock)., create_modbus_backend(), MockModbusBackend, ModbusBackend, ModbusReadError (+9 more)

### Community 2 - "MeterReading"
Cohesion: 0.09
Nodes (14): Ring buffer per meter untuk konsumsi frontend (live view).  Selalu menerima samp, Buffer FIFO thread-safe berbasis `deque(maxlen=...)`.      Sample tertua otomati, Salinan isi buffer (tidak mengosongkan buffer)., RingBuffer, Polling loop Modbus per meter (asyncio task).  Tiap cycle (default 500ms): baca, Publish sample buffer & device info ke Redis untuk konsumsi service API terpisah, Async Redis client untuk mirror ring buffer & device info., RedisPublisher (+6 more)

### Community 3 - "Database"
Cohesion: 0.20
Nodes (6): _as_uuid(), Database, datetime, Lapisan PostgreSQL via asyncpg.  - Insert per sample (real-time, bukan batch) sa, Wrapper connection pool asyncpg., UUID

### Community 4 - "settings.py"
Cohesion: 0.19
Nodes (12): BaseSettings, _assert_unique(), get_settings(), load_meters(), load_registers(), Konfigurasi global aplikasi.  Parameter dibaca dari environment / file `.env` vi, Muat daftar meter dari YAML. Raise kalau file/format invalid., Muat register map dari YAML. Raise kalau file/format invalid. (+4 more)

### Community 5 - "Application"
Cohesion: 0.18
Nodes (6): _amain(), Application, main(), Entry point Energy Meter Collection System.  Orkestrasi: load config → init GPIO, Konfigurasi structlog → output JSON ke stdout., setup_logging()

### Community 6 - "RegisterDef"
Cohesion: 0.18
Nodes (10): BaseModel, Definisi satu register dari `registers.yaml`., RegisterDef, decode_register(), Decode raw Modbus register (list 16-bit words) menjadi nilai numerik.  Versi-ind, Decode satu nilai dari list word mentah. Return None kalau gagal., Decode blok response Modbus berdasarkan register map.      Sebuah "blok" adalah, Ekstrak semua register yang alamatnya jatuh di dalam blok ini. (+2 more)

### Community 7 - "ModbusPoller"
Cohesion: 0.33
Nodes (4): ModbusPoller, Baca register device_info sekali (best-effort)., Satu poller per meter., Entry point asyncio task.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `GPIOHandler` connect `GPIOHandler` to `MeterConfig`, `MeterReading`, `Application`, `RegisterDef`, `ModbusPoller`?**
  _High betweenness centrality (0.262) - this node is a cross-community bridge._
- **Why does `ModbusPoller` connect `ModbusPoller` to `GPIOHandler`, `MeterConfig`, `MeterReading`, `Application`, `RegisterDef`?**
  _High betweenness centrality (0.236) - this node is a cross-community bridge._
- **Why does `MeterConfig` connect `MeterConfig` to `GPIOHandler`, `settings.py`, `RegisterDef`, `ModbusPoller`?**
  _High betweenness centrality (0.137) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `GPIOHandler` (e.g. with `MeterConfig` and `ModbusPoller`) actually correct?**
  _`GPIOHandler` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `ModbusPoller` (e.g. with `MeterConfig` and `RegisterDef`) actually correct?**
  _`ModbusPoller` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `RegisterDef` (e.g. with `MockModbusBackend` and `ModbusBackend`) actually correct?**
  _`RegisterDef` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `MeterConfig` (e.g. with `GPIOHandler` and `State`) actually correct?**
  _`MeterConfig` has 7 INFERRED edges - model-reasoned connections that need verification._