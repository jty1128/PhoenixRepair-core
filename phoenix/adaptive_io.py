"""Adjust admission from sustained host I/O pressure, without killing agents."""
from pathlib import Path
import time


class IOSampler:
    def __init__(self, device='sdc'):
        self.device = device
        self.previous = None

    def sample(self):
        now = time.monotonic()
        cpu = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
        disk = None
        for line in Path('/proc/diskstats').read_text().splitlines():
            words = line.split()
            if words[2] == self.device:
                disk = list(map(int, words[3:])); break
        if disk is None:
            return None
        current = (now, cpu, disk)
        previous, self.previous = self.previous, current
        if previous is None:
            return None
        elapsed = now - previous[0]
        if elapsed <= 0:
            return None
        c = [b - a for a, b in zip(previous[1], cpu)]
        d = [b - a for a, b in zip(previous[2], disk)]
        operations = d[0] + d[4]
        return {'device': self.device, 'interval_seconds': elapsed,
                'iowait_pct': 100 * c[4] / max(1, sum(c)),
                'cpu_busy_pct': 100 * (sum(c) - c[3] - c[4]) / max(1, sum(c)),
                'memory_available_GiB': next(int(line.split()[1]) / 1024**2 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')),
                'disk_util_pct': min(100, 100 * d[9] / (elapsed * 1000)),
                'await_ms': (d[3] + d[7]) / max(1, operations),
                'queue_depth': d[10] / (elapsed * 1000)}


class AdmissionPolicy:
    def __init__(self, initial=12, maximum=30, minimum=4):
        self.minimum = min(minimum, maximum)
        self.maximum = maximum
        self.limit = max(self.minimum, min(initial, maximum))
        self.low_since = None
        self.high_since = None
        self.last_change = None
        self.last_failure = None
        self.last = None
        self.reason = 'initial_limit'
        self.last_request_id = None

    def apply_request(self, request, wall_time):
        if not request or request.get('id') == self.last_request_id or request.get('expires_at', 0) < wall_time:
            return False
        target = request.get('total')
        if type(target) is not int or not self.minimum <= target <= self.maximum:
            return False
        self.limit = target
        self.last_request_id = request['id']
        self.last_change = None
        self.reason = 'user_capacity_probe'
        return True

    def update(self, metrics, now, active, infrastructure_failure=False):
        if infrastructure_failure:
            self.last_failure = now
        if metrics is None:
            return self.settings()
        self.last = metrics
        # Host iowait includes unrelated disks and other users' jobs. Require
        # pressure on the selected experiment device before using that signal.
        device_busy = metrics['await_ms'] >= 25 or metrics['queue_depth'] >= 12
        host_pressure = (metrics.get('memory_available_GiB', 100) < 24 or
                         metrics.get('cpu_busy_pct', 0) >= 90)
        high = (host_pressure or (metrics['iowait_pct'] >= 15 and device_busy) or
                (metrics['disk_util_pct'] >= 85 and
                 (metrics['await_ms'] >= 60 or metrics['queue_depth'] >= 24)))
        critical = ((metrics['iowait_pct'] >= 30 and device_busy) or
                    metrics.get('memory_available_GiB', 100) < 12 or
                    (metrics['disk_util_pct'] >= 90 and metrics['await_ms'] >= 150))
        low = (not host_pressure and metrics['disk_util_pct'] < 85 and
               metrics['await_ms'] < 25 and metrics['queue_depth'] < 12)
        self.high_since = (now if self.high_since is None else self.high_since) if high else None
        self.low_since = (now if self.low_since is None else self.low_since) if low else None
        cooldown = self.last_change is None or now - self.last_change >= 60
        if high and cooldown and now - self.high_since >= 30:
            self.limit = max(self.minimum, self.limit - (4 if critical else 2))
            self.last_change = now
            self.reason = 'sustained_critical_io' if critical else 'sustained_high_io'
            self.low_since = None
        elif (low and self.low_since is not None and now - self.low_since >= 600
              and (self.last_change is None or now - self.last_change >= 600)
              and (self.last_failure is None or now - self.last_failure >= 600)
              and active >= self.limit - 1 and self.limit < self.maximum):
            self.limit = min(self.maximum, self.limit + 2)
            self.last_change = now
            self.low_since = now
            self.reason = 'ten_minutes_stable_io'
        return self.settings(high=high)

    def settings(self, high=False):
        return {'mode': 'adaptive_io', 'total': self.limit, 'per_model': (self.limit + 1) // 2,
                'minimum': self.minimum, 'maximum': self.maximum,
                'environment_start_concurrency': 1 if high else 2,
                'local_command_concurrency': 2 if high else 4,
                'pause_new_image_imports': high, 'reason': self.reason,
                'last_request_id': self.last_request_id,
                'sample': self.last, 'evaluation_enabled': False,
                'running_workers_are_preserved': True, 'updated_at': time.time()}
