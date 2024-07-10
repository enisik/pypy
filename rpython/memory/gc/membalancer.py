class MemBalancer(object):
    from math import sqrt
    s_m_smoothed = 2 * 1024**2  # bytes collected
    s_t_smoothed = 1.0 / 1024     # collection time
    L_smoothed = 0.0            # live memory
    g_m_smoothed = 0.0          # memory allocated since last heartbeat
    g_t_smoothed = 0.0          # time since last heartbeat
    heap_limit = 0.0

    def __init__(
            self,
            heap_limit,
            SMOOTHING_FACTOR_G=0.95,
            SMOOTHING_FACTOR_S=0.5,
            TUNING=1,  # MemBalancer tuning parameter
            minimum_extra_heap=2000,
            nursery_size=10000):
        self.heap_limit = heap_limit
        self.SMOOTHING_FACTOR_G = SMOOTHING_FACTOR_G
        self.SMOOTHING_FACTOR_S = SMOOTHING_FACTOR_S
        self.TUNING = TUNING
        self.minimum_extra_heap = minimum_extra_heap
        self.nursery_size = nursery_size
        self.seen_first_major_gc = False

    def on_gc(self, s_m, s_t, L):
        self.seen_first_major_gc = True
        self.s_m_smoothed = self.SMOOTHING_FACTOR_S * self.s_m_smoothed \
            + (1 - self.SMOOTHING_FACTOR_S) * s_m
        self.s_t_smoothed = self.SMOOTHING_FACTOR_S * self.s_t_smoothed \
            + (1 - self.SMOOTHING_FACTOR_S) * s_t
        self.L_smoothed = L

    def on_heartbeat(self, g_m, g_t):
        self.g_m_smoothed = self.SMOOTHING_FACTOR_G * self.g_m_smoothed \
            + (1 - self.SMOOTHING_FACTOR_G) * g_m
        self.g_t_smoothed = self.SMOOTHING_FACTOR_G * self.g_t_smoothed \
            + (1 - self.SMOOTHING_FACTOR_G) * g_t

    def compute_M(self):
        if not self.seen_first_major_gc: return self.heap_limit
        E = self.sqrt(self.L_smoothed / self.TUNING
                      * (self.g_m_smoothed / self.g_t_smoothed)
                      / (self.s_m_smoothed / self.s_t_smoothed))
        self.E = E
        self.heap_limit = self.L_smoothed + \
            max(E, self.minimum_extra_heap) + self.nursery_size
        return self.heap_limit