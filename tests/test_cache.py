import threading
import time
from easynews_indexer.cache import SingleFlight, TTLCache


def test_ttl_cache_expires():
    c = TTLCache(2); c.set("x", 1, .01); assert c.get("x") == 1; time.sleep(.02); assert c.get("x") is None


def test_singleflight_coalesces():
    sf = SingleFlight(); calls = []
    def work(): calls.append(1); time.sleep(.03); return 7
    out=[]
    threads=[threading.Thread(target=lambda: out.append(sf.run("k", work))) for _ in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert out == [7,7,7,7]
    assert len(calls) == 1
