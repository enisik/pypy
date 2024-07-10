from __future__ import print_function
from rpython.memory.gc.membalancer import MemBalancer

def test_stable():
    heap_limit = 10 * 1024**2
    mem_balancer = MemBalancer(heap_limit=heap_limit, nursery_size=4 * 1024**2)
    #import pdb; pdb.set_trace()
    current_memory_use = 0
    g_m = 500 * 1024
    timestamp_of_collect = []
    for i in range(10000):
        mem_balancer.on_heartbeat(g_m, 0.1)
        heap_limit = mem_balancer.compute_M()
        current_memory_use += g_m
        if current_memory_use > heap_limit:
            timestamp_of_collect.append(i)
            print(i, current_memory_use, heap_limit, mem_balancer.s_m_smoothed)
            new_mem_used = 1024**2
            mem_balancer.on_gc(current_memory_use-new_mem_used, 20.0/1000, new_mem_used)
            heap_limit = mem_balancer.compute_M()
            current_memory_use = new_mem_used
    for i in range(1, len(timestamp_of_collect)):
        timestamp_of_collect[i - 1] = timestamp_of_collect[i] - timestamp_of_collect[i - 1]
    del timestamp_of_collect[-1]
    # converges to a stable situation
    assert len(set(timestamp_of_collect[-100:])) == 1


def test_growing():
    heap_limit = 10 * 1024**2
    mem_balancer = MemBalancer(heap_limit=heap_limit, TUNING=0.001, nursery_size=4 * 1024**2)
    # import pdb; pdb.set_trace()
    current_memory_use = 0
    g_m = 500 * 1024
    timestamp_of_collect = []
    for i in range(10000):
       
        mem_balancer.on_heartbeat(g_m, 0.1)
        heap_limit = mem_balancer.compute_M()
        current_memory_use += g_m
        if current_memory_use > heap_limit:
            timestamp_of_collect.append(i)
            print(i, current_memory_use, heap_limit)
            new_mem_used = current_memory_use // 10
            mem_balancer.on_gc(current_memory_use -
                               new_mem_used, 20.0 / 1000, new_mem_used)
            heap_limit = mem_balancer.compute_M()
            current_memory_use = new_mem_used
    for i in range(1, len(timestamp_of_collect)):
        timestamp_of_collect[i - 1] = timestamp_of_collect[i] - \
            timestamp_of_collect[i - 1]
    del timestamp_of_collect[-1]
    # converges to a stable situation
    assert len(set(timestamp_of_collect[-100:])) == 1