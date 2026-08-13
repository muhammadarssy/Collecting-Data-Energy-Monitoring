import asyncio
from pymodbus.client import AsyncModbusSerialClient

BAUDRATE = 9600
POLL_INTERVAL = 0.5

# Address integer (1 register = 1 nilai uint16)
ADDRESSES = [6, 7]

# Beberapa kombinasi PORT + SLAVE — sesuaikan sesuai perangkat
TARGETS = [
    # {"port": "/dev/ttyACM2", "slave_id": 2},
    {"port": "/dev/ttyACM1", "slave_id": 6},
    {"port": "/dev/ttyACM0", "slave_id": 5},
    {"port": "/dev/ttyACM7", "slave_id": 4},
    {"port": "/dev/ttyACM6", "slave_id": 3},
]


async def poll_target(port: str, slave_id: int) -> None:
    client = AsyncModbusSerialClient(
        port=port,
        baudrate=BAUDRATE,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=1,
    )

    await client.connect()
    if not client.connected:
        print(f"[{port} slave={slave_id}] Gagal konek")
        return

    print(f"[{port} slave={slave_id}] Connected")

    try:
        while True:
            for address in ADDRESSES:
                result = await client.read_holding_registers(
                    address=address,
                    count=1,
                    slave=slave_id,
                )

                if result.isError():
                    print(f"[{port} slave={slave_id}] Addr {address} error: {result}")
                else:
                    value = int(result.registers[0])
                    print(
                        f"[{port} slave={slave_id}] "
                        f"Addr {address} | Raw: {result.registers} | Value: {value}"
                    )

            await asyncio.sleep(POLL_INTERVAL)
    except asyncio.CancelledError:
        raise
    finally:
        client.close()
        print(f"[{port} slave={slave_id}] Disconnected")


async def main() -> None:
    if not TARGETS:
        print("TARGETS kosong — isi PORT dan SLAVE dulu")
        return

    tasks = [
        asyncio.create_task(poll_target(t["port"], t["slave_id"]))
        for t in TARGETS
    ]

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("Stop polling")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
