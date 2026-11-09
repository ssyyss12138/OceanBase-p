"""Wait for one declared HTTP readiness endpoint, with a bounded deadline."""

import argparse
import time
import httpx

p = argparse.ArgumentParser()
p.add_argument("url")
p.add_argument("--seconds", type=int, default=180)
a = p.parse_args()
end = time.monotonic() + a.seconds
with httpx.Client(timeout=3, trust_env=False) as c:
    while time.monotonic() < end:
        try:
            r = c.get(a.url)
            if r.status_code == 200:
                print("Ready:", a.url)
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise SystemExit("Service did not become ready before deadline: " + a.url)
