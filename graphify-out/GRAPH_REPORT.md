# Graph Report - 05_Collecting Data  (2026-07-27)

## Corpus Check
- 18 files · ~11,054 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 233 nodes · 415 edges · 10 communities
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 44 edges (avg confidence: 0.59)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1d0853e7`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- GPIOHandler
- MeterConfig
- MeterReading
- Database
- settings.py
- Application
- ModbusPoller
- Module Detail
- Storage Schema — Redis & PostgreSQL

## God Nodes (most connected - your core abstractions)
1. `GPIOHandler` - 29 edges
2. `ModbusPoller` - 25 edges
3. `MeterConfig` - 20 edges
4. `RegisterDef` - 18 edges
5. `MeterReading` - 18 edges
6. `Database` - 16 edges
7. `ModbusBackend` - 15 edges
8. `RedisPublisher` - 15 edges
9. `Application` - 15 edges
10. `RingBuffer` - 13 edges

## Surprising Connections (you probably didn't know these)
- `GPIOHandler` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/gpio_handler.py → energy-collector/config/settings.py
- `State` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/gpio_handler.py → energy-collector/config/settings.py
- `ModbusPoller` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/modbus_client.py → energy-collector/config/settings.py
- `ModbusPoller` --uses--> `RegisterDef`  [INFERRED]
  energy-collector/core/modbus_client.py → energy-collector/config/settings.py
- `RegisterParser` --uses--> `RegisterDef`  [INFERRED]
  energy-collector/core/register_parser.py → energy-collector/config/settings.py

## Import Cycles
- None detected.

## Communities (10 total, 0 thin omitted)

### Community 0 - "GPIOHandler"
Cohesion: 0.09
Nodes (17): CycleCloseHook, CycleOpenHook, cleanup_gpio(), _gpio_setup_hints(), GPIOHandler, init_gpio(), _new_id(), _now() (+9 more)

### Community 1 - "MeterConfig"
Cohesion: 0.08
Nodes (20): ABC, BaseModel, MeterConfig, Definisi satu register dari `registers.yaml`., Konfigurasi satu meter dari `meters.yaml`.      `type` / `device_type`:       -, RegisterDef, Hardware abstraction layer — Modbus backend (asli / mock)., create_modbus_backend() (+12 more)

### Community 2 - "MeterReading"
Cohesion: 0.10
Nodes (10): Buffer FIFO thread-safe berbasis `deque(maxlen=...)`.      Sample tertua otomati, Salinan isi buffer (tidak mengosongkan buffer)., RingBuffer, Async Redis client untuk mirror ring buffer & device info., RedisPublisher, _serialize_reading(), MeterReading, Satu sample pembacaan dari satu meter.      Live (buffer/Redis): session_id life (+2 more)

### Community 3 - "Database"
Cohesion: 0.20
Nodes (6): _as_uuid(), Database, datetime, Lapisan PostgreSQL via asyncpg.  - Insert per sample (real-time, bukan batch) sa, Wrapper connection pool asyncpg., UUID

### Community 4 - "settings.py"
Cohesion: 0.18
Nodes (12): BaseSettings, _assert_unique(), get_settings(), load_meters(), load_registers(), Konfigurasi global aplikasi.  Parameter dibaca dari environment / file `.env` vi, Muat daftar meter dari YAML. Raise kalau file/format invalid., Muat register map dari YAML. Raise kalau file/format invalid. (+4 more)

### Community 5 - "Application"
Cohesion: 0.13
Nodes (14): Ring buffer per meter untuk konsumsi frontend (live view).  Selalu menerima samp, Polling loop Modbus per meter (asyncio task).  Tiap cycle (default 500ms): baca, Publish sample buffer & device info ke Redis untuk konsumsi service API terpisah, decode_register(), Decode raw Modbus register (list 16-bit words) menjadi nilai numerik.  Versi-ind, Decode satu nilai dari list word mentah. Return None kalau gagal., Ekstrak semua register yang alamatnya jatuh di dalam blok ini., _words_to_bytes() (+6 more)

### Community 7 - "ModbusPoller"
Cohesion: 0.14
Nodes (9): ModbusPoller, (gpio_state, session_id, cycle_id). Session selalu dari lifetime app., Insert 1 snapshot ke DB tiap interval (dengan session, tanpa cycle)., Baca register device_info sekali (best-effort)., Satu poller per meter., Entry point asyncio task., Decode blok response Modbus berdasarkan register map.      Sebuah "blok" adalah, RegisterParser (+1 more)

### Community 9 - "Module Detail"
Cohesion: 0.07
Nodes (26): Alur Program, Catatan Final, `config/meters.yaml`, `config/registers.yaml`, `config/settings.py`, Contoh `.env`, `core/buffer.py`, `core/db.py` (+18 more)

### Community 10 - "Storage Schema — Redis & PostgreSQL"
Cohesion: 0.13
Nodes (14): 0. Tipe Device (`device_type`), 1.1 Pola Key, 1.2 `latest` (String) & `readings` (List), 1.3 `device_info` (Hash), 1.4 Contoh Akses (untuk Endpoint), 1. Redis, 2.1 Tabel `meter_readings`, 2.2 Tabel `production_sessions` (+6 more)

## Knowledge Gaps
- **34 isolated node(s):** `0. Tipe Device (`device_type`)`, `1.1 Pola Key`, `1.2 `latest` (String) & `readings` (List)`, `1.3 `device_info` (Hash)`, `1.4 Contoh Akses (untuk Endpoint)` (+29 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ModbusPoller` connect `ModbusPoller` to `GPIOHandler`, `MeterConfig`, `MeterReading`, `Application`?**
  _High betweenness centrality (0.187) - this node is a cross-community bridge._
- **Why does `GPIOHandler` connect `GPIOHandler` to `MeterConfig`, `Application`, `ModbusPoller`?**
  _High betweenness centrality (0.172) - this node is a cross-community bridge._
- **Why does `MeterConfig` connect `MeterConfig` to `GPIOHandler`, `settings.py`, `ModbusPoller`?**
  _High betweenness centrality (0.105) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `GPIOHandler` (e.g. with `MeterConfig` and `ModbusPoller`) actually correct?**
  _`GPIOHandler` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `ModbusPoller` (e.g. with `MeterConfig` and `RegisterDef`) actually correct?**
  _`ModbusPoller` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `MeterConfig` (e.g. with `GPIOHandler` and `State`) actually correct?**
  _`MeterConfig` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `RegisterDef` (e.g. with `MockModbusBackend` and `ModbusBackend`) actually correct?**
  _`RegisterDef` has 6 INFERRED edges - model-reasoned connections that need verification._