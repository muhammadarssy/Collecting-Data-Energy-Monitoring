import RPi.GPIO as GPIO
import time

# Gunakan nomor GPIO BCM
GPIO.setmode(GPIO.BCM)

# GPIO yang ingin dicek
pins = [2, 3, 14, 15]

# Set sebagai input
for pin in pins:
    GPIO.setup(pin, GPIO.IN)

try:
    while True:
        print("===== GPIO Status =====")
        for pin in pins:
            status = GPIO.input(pin)
            print(f"GPIO {pin}: {'HIGH' if status else 'LOW'}")
        print()
        time.sleep(1)

except KeyboardInterrupt:
    print("Program dihentikan")

finally:
    GPIO.cleanup()