# DS18B20 1-Wire temperature probes: any number on one GPIO (4.7 kOhm pull-up from data to 3V3).
# Probes are found at start-up (one channel each); after adding a probe, save the sensor once in
# the Sensors panel or restart. Non-blocking: conversion started on one read (750 ms at 12 bit),
# fetched on the next (~2 s later).
import time
from machine import Pin
import onewire
import ds18x20


class Driver:
    def __init__(self, e, ctx=None):
        self.e = e
        self.ds = ds18x20.DS18X20(onewire.OneWire(Pin(e['pin'])))
        self.roms = self.ds.scan()
        if not self.roms:
            raise OSError('no DS18B20 found on GPIO%d (pull-up 4.7k?)' % e['pin'])
        self.last = [None] * len(self.roms)
        self.t = None                      # ticks_ms when the running conversion started

    def chans(self):
        n = len(self.roms)
        return [('t%d' % (k + 1), self.e['name'] + (' #%d' % (k + 1) if n > 1 else ''), '&deg;C', 1)
                for k in range(n)]

    def read(self):
        now = time.ticks_ms()
        if self.t is not None and time.ticks_diff(now, self.t) < 800:
            return list(self.last)         # conversion still running (fast sleep-cycle reads)
        if self.t is not None:
            for k, r in enumerate(self.roms):
                try:
                    t = self.ds.read_temp(r)
                    self.last[k] = None if t is None or t == 85.0 else t   # 85.0 = power-on value
                except Exception:
                    self.last[k] = None
        self.ds.convert_temp()
        self.t = now
        return list(self.last)

    def info(self):
        return 'GPIO%d &middot; %d probe(s)' % (self.e['pin'], len(self.roms))
