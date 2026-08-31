# Graph Report - 05_Collecting Data  (2026-08-31)

## Corpus Check
- 22 files · ~13,833 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 283 nodes · 495 edges · 15 communities (13 shown, 2 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 48 edges (avg confidence: 0.59)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `d1563c32`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- GPIOHandler
- MeterConfig
- MeterReading
- Database
- settings.py
- RedisPublisher
- register_parser.py
- ModbusPoller
- Module Detail
- Storage Schema — Redis & PostgreSQL
- Application
- Application
- migrasi.py
- register_parser.py

## God Nodes (most connected - your core abstractions)
1. `GPIOHandler` - 29 edges
2. `ModbusPoller` - 25 edges
3. `Database` - 22 edges
4. `MeterConfig` - 20 edges
5. `MeterReading` - 20 edges
6. `RegisterDef` - 18 edges
7. `ModbusBackend` - 15 edges
8. `RedisPublisher` - 15 edges
9. `Application` - 15 edges
10. `RingBuffer` - 13 edges

## Surprising Connections (you probably didn't know these)
- `GPIOHandler` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/gpio_handler.py → energy-collector/config/settings.py
- `State` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/gpio_handler.py → energy-collector/config/settings.py
- `MockModbusBackend` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/hardware/modbus_backend.py → energy-collector/config/settings.py
- `ModbusBackend` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/hardware/modbus_backend.py → energy-collector/config/settings.py
- `ModbusReadError` --uses--> `MeterConfig`  [INFERRED]
  energy-collector/core/hardware/modbus_backend.py → energy-collector/config/settings.py

## Import Cycles
- None detected.

## Communities (15 total, 2 thin omitted)

### Community 0 - "GPIOHandler"
Cohesion: 0.10
Nodes (15): cleanup_gpio(), _gpio_setup_hints(), GPIOHandler, init_gpio(), _new_id(), _now(), datetime, State machine cycle per pin GPIO (per meter).  Optocoupler open-collector: pin d (+7 more)

### Community 1 - "MeterConfig"
Cohesion: 0.09
Nodes (15): ABC, Hardware abstraction layer — Modbus backend (asli / mock)., create_modbus_backend(), MockModbusBackend, ModbusBackend, ModbusReadError, PymodbusBackend, Backend Modbus: pymodbus AsyncModbusSerialClient asli atau mock.  Interface sera (+7 more)

### Community 2 - "MeterReading"
Cohesion: 0.10
Nodes (10): Buffer FIFO thread-safe berbasis `deque(maxlen=...)`.      Sample tertua otomati, Salinan isi buffer (tidak mengosongkan buffer)., RingBuffer, Async Redis client untuk mirror ring buffer & device info., RedisPublisher, _serialize_reading(), MeterReading, Satu sample pembacaan dari satu meter.      Live (buffer/Redis): session_id life (+2 more)

### Community 3 - "Database"
Cohesion: 0.15
Nodes (11): _as_uuid(), Database, _dt(), Any, datetime, Lapisan PostgreSQL via asyncpg + outbox lokal.  - Insert per sample (real-time,, Wrapper connection pool asyncpg + store-and-forward spool., Reconnect + flush spool sampai di-cancel. (+3 more)

### Community 4 - "settings.py"
Cohesion: 0.09
Nodes (22): BaseModel, BaseSettings, CycleCloseHook, CycleOpenHook, _assert_unique(), get_settings(), load_meters(), load_registers() (+14 more)

### Community 5 - "RedisPublisher"
Cohesion: 0.17
Nodes (10): Ring buffer per meter untuk konsumsi frontend (live view).  Selalu menerima samp, Polling loop Modbus per meter (asyncio task).  Tiap cycle (default 500ms): baca, Publish sample buffer & device info ke Redis untuk konsumsi service API terpisah, decode_register(), Decode raw Modbus register (list 16-bit words) menjadi nilai numerik.  Versi-ind, Ekstrak semua register yang alamatnya jatuh di dalam blok ini., Decode satu nilai dari list word mentah. Return None kalau gagal., _words_to_bytes() (+2 more)

### Community 6 - "register_parser.py"
Cohesion: 0.18
Nodes (8): ModbusPoller, (gpio_state, session_id, cycle_id). Session selalu dari lifetime app., Insert 1 snapshot ke DB tiap interval (dengan session, tanpa cycle)., Baca register device_info sekali (best-effort)., Satu poller per meter., Entry point asyncio task., apply_meter_conversion(), Konversi raw → engineering sesuai rumus UrAt/IrAt.      U  = URMS × (UrAt×0.1)×0

### Community 9 - "Module Detail"
Cohesion: 0.07
Nodes (26): Alur Program, Catatan Final, `config/meters.yaml`, `config/registers.yaml`, `config/settings.py`, Contoh `.env`, `core/buffer.py`, `core/db.py` (+18 more)

### Community 10 - "Storage Schema — Redis & PostgreSQL"
Cohesion: 0.13
Nodes (14): 0. Tipe Device (`device_type`), 1.1 Pola Key, 1.2 `latest` (String) & `readings` (List), 1.3 `device_info` (Hash), 1.4 Contoh Akses (untuk Endpoint), 1. Redis, 2.1 Tabel `meter_readings`, 2.2 Tabel `production_sessions` (+6 more)

### Community 12 - "Application"
Cohesion: 0.23
Nodes (5): _amain(), Application, main(), Konfigurasi structlog → output JSON ke stdout., setup_logging()

### Community 13 - "migrasi.py"
Cohesion: 0.21
Nodes (17): ArgumentParser, Connection, build_parser(), _chunks(), _insert_rows(), _insert_sql(), main(), _meter_sql() (+9 more)

### Community 14 - "register_parser.py"
Cohesion: 0.17
Nodes (5): Path, LocalSpool, Any, Path, Outbox SQLite lokal untuk store-and-forward ke PostgreSQL.  Tahan crash proses (

## Knowledge Gaps
- **35 isolated node(s):** `install-service.sh script`, `0. Tipe Device (`device_type`)`, `1.1 Pola Key`, `1.2 `latest` (String) & `readings` (List)`, `1.3 `device_info` (Hash)` (+30 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ModbusPoller` connect `register_parser.py` to `GPIOHandler`, `MeterConfig`, `MeterReading`, `settings.py`, `RedisPublisher`, `Application`?**
  _High betweenness centrality (0.148) - this node is a cross-community bridge._
- **Why does `GPIOHandler` connect `GPIOHandler` to `Application`, `settings.py`, `RedisPublisher`, `register_parser.py`?**
  _High betweenness centrality (0.135) - this node is a cross-community bridge._
- **Why does `Database` connect `Database` to `MeterReading`, `Application`, `RedisPublisher`, `register_parser.py`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `GPIOHandler` (e.g. with `MeterConfig` and `ModbusPoller`) actually correct?**
  _`GPIOHandler` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `ModbusPoller` (e.g. with `MeterConfig` and `RegisterDef`) actually correct?**
  _`ModbusPoller` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Database` (e.g. with `LocalSpool` and `MeterReading`) actually correct?**
  _`Database` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `MeterConfig` (e.g. with `GPIOHandler` and `State`) actually correct?**
  _`MeterConfig` has 7 INFERRED edges - model-reasoned connections that need verification._