"""Local microphone probe for Amadeus voice call debugging."""
from __future__ import annotations

import argparse
import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--device", type=int, default=None)
    args = parser.parse_args()

    import sounddevice as sd

    devices = list(sd.query_devices())
    print(f"default input: {sd.default.device[0]}")
    input_devices = []
    for i, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) > 0:
            input_devices.append(i)
            print(
                f"[{i}] {dev['name']} inputs={dev['max_input_channels']} "
                f"default_sr={dev['default_samplerate']:.0f}"
            )
    if not input_devices:
        print("ERROR: no input device found")
        return 2

    device = args.device
    if device is None:
        default_in = sd.default.device[0]
        device = default_in if default_in in input_devices else input_devices[0]
    info = sd.query_devices(device)
    sr = int(info["default_samplerate"])
    print(f"recording device={device} sr={sr} seconds={args.seconds}")
    audio = sd.rec(int(args.seconds * sr), samplerate=sr, channels=1, dtype="float32", device=device)
    sd.wait()
    samples = np.asarray(audio).reshape(-1)
    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if samples.size else 0.0
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    print(f"samples={samples.size} rms={rms:.6f} peak={peak:.6f}")
    if rms < 0.001 and peak < 0.005:
        print("WARN: input is nearly silent. Check Windows microphone permission, selected device, or mute state.")
        return 1
    print("OK: microphone produced non-silent input")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
