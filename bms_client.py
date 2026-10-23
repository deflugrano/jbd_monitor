"""
JBD (Xiaoxiang) Smart BMS — полный BLE-клиент.
Читает ВСЕ доступные регистры: базовые параметры, ячейки, ошибки,
статус MOS-FET, балансировку, температуры, версию прошивки.
"""

import asyncio
import struct
import time
import math
from collections import deque
from bleak import BleakClient, BleakScanner

# ─── BLE UUID ────────────────────────────────────────────────
WRITE_UUID  = "0000ff02-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"

# ─── Команды JBD BMS ────────────────────────────────────────
CMD_BASIC   = bytes([0xDD, 0xA5, 0x03, 0x00, 0xFF, 0xFD, 0x77])
CMD_CELLS   = bytes([0xDD, 0xA5, 0x04, 0x00, 0xFF, 0xFC, 0x77])
CMD_HWVER   = bytes([0xDD, 0xA5, 0x05, 0x00, 0xFF, 0xFB, 0x77])

# ─── Таблица ошибок / защит JBD ─────────────────────────────
PROTECTION_FLAGS = {
    0:  {"name": "Перенапряжение ячейки",       "icon": "⚡", "severity": "danger"},
    1:  {"name": "Пониженное напряж. ячейки",   "icon": "🪫", "severity": "danger"},
    2:  {"name": "Перенапряжение пакета",       "icon": "🔋", "severity": "danger"},
    3:  {"name": "Пониженное напряж. пакета",   "icon": "🪫", "severity": "danger"},
    4:  {"name": "Перегрев при заряде",         "icon": "🌡️", "severity": "danger"},
    5:  {"name": "Переохлажд. при заряде",      "icon": "❄️", "severity": "warning"},
    6:  {"name": "Перегрев при разряде",        "icon": "🔥", "severity": "danger"},
    7:  {"name": "Переохлажд. при разряде",     "icon": "❄️", "severity": "warning"},
    8:  {"name": "Превышение тока заряда",      "icon": "⚡", "severity": "danger"},
    9:  {"name": "Превышение тока разряда",     "icon": "⚡", "severity": "danger"},
    10: {"name": "Короткое замыкание",          "icon": "💥", "severity": "critical"},
    11: {"name": "Ошибка микросхемы (IC)",      "icon": "🔧", "severity": "critical"},
    12: {"name": "MOS заблокирован софтом",     "icon": "🔒", "severity": "warning"},
}

# Максимальная длина истории (≈30 минут при опросе каждые 3 сек)
HISTORY_LEN = 600


class JBDReader:
    def __init__(self, mac_address: str):
        self.mac = mac_address
        self._buf = bytearray()
        self._event = asyncio.Event()
        self._start_time = time.time()

        # ─── Текущие данные ──────────────────────────────────
        self.data = {
            # Основные
            "voltage": 0.0,
            "current": 0.0,
            "power": 0.0,
            "soc": 0,
            "capacity_remain": 0.0,
            "capacity_total": 0.0,
            "cycles": 0,
            "num_cells": 0,

            # Направление и время
            "direction": "idle",
            "time_remaining_str": "—",
            "time_remaining_min": 0,

            # Температуры
            "temps": [],
            "temp_max": 0.0,
            "temp_min": 0.0,
            "temp_avg": 0.0,

            # Ячейки
            "cells": [],
            "cell_min": 0.0,
            "cell_max": 0.0,
            "cell_delta_mv": 0,
            "cell_avg": 0.0,

            # Баланс
            "balance_status": [],

            # MOS FET статус
            "charge_mosfet": False,
            "discharge_mosfet": False,

            # Ошибки / Защита
            "protection_raw": 0,
            "errors": [],
            "has_errors": False,

            # Версия прошивки
            "hw_version": "—",
            "sw_version": 0,
            "production_date": "—",

            # Энергия (Вт·ч)
            "energy_remain_wh": 0.0,
            "energy_total_wh": 0.0,

            # Здоровье
            "soh_estimate": 100.0,

            # Связь
            "status": "Ожидание подключения...",
            "last_update": 0,
            "uptime_str": "0 мин",
            "connection_errors": 0,

            # История (для графиков)
            "history_ts": [],
            "history_voltage": [],
            "history_current": [],
            "history_power": [],
            "history_soc": [],
            "history_temp": [],
        }

        # Внутренние deque для хранения истории
        self._h_ts   = deque(maxlen=HISTORY_LEN)
        self._h_v    = deque(maxlen=HISTORY_LEN)
        self._h_i    = deque(maxlen=HISTORY_LEN)
        self._h_p    = deque(maxlen=HISTORY_LEN)
        self._h_soc  = deque(maxlen=HISTORY_LEN)
        self._h_temp = deque(maxlen=HISTORY_LEN)

    # ─── BLE Callback ───────────────────────────────────────
    def _notification_handler(self, sender, raw: bytearray):
        self._buf.extend(raw)
        if len(self._buf) >= 4 and self._buf[-1] == 0x77:
            self._parse_packet(bytes(self._buf))
            self._buf.clear()
            self._event.set()

    # ─── Парсер пакетов ─────────────────────────────────────
    def _parse_packet(self, pkt: bytes):
        if len(pkt) < 7 or pkt[0] != 0xDD:
            return
        cmd    = pkt[1]
        status = pkt[2]
        length = pkt[3]
        payload = pkt[4:4 + length]

        if status != 0x00:
            return

        if cmd == 0x03:
            self._parse_basic(payload)
        elif cmd == 0x04:
            self._parse_cells(payload)
        elif cmd == 0x05:
            self._parse_hwver(payload)

    # ─── 0x03: Основные параметры ────────────────────────────
    def _parse_basic(self, p: bytes):
        if len(p) < 23:
            return

        v_raw, i_raw = struct.unpack(">Hh", p[0:4])
        cap_rem, cap_tot = struct.unpack(">HH", p[4:8])
        cycles = struct.unpack(">H", p[8:10])[0]
        prod_date = struct.unpack(">H", p[10:12])[0]

        # Balance status (32-bit across two 16-bit words)
        bal_lo, bal_hi = struct.unpack(">HH", p[12:16])
        balance_bits = (bal_hi << 16) | bal_lo

        # Protection status
        prot_raw = struct.unpack(">H", p[16:18])[0]

        sw_ver    = p[18]
        soc       = p[19]
        fet_ctrl  = p[20]
        num_cells = p[21]
        num_ntc   = p[22]

        # ─── Расчёты ────────────
        voltage = round(v_raw / 100.0, 2)
        current = round(i_raw / 100.0, 2)
        power   = round(voltage * abs(current), 1)

        # Температуры
        temps = []
        for k in range(num_ntc):
            off = 23 + k * 2
            if off + 2 <= len(p):
                t_raw = struct.unpack(">H", p[off:off + 2])[0]
                temps.append(round((t_raw - 2731) / 10.0, 1))

        temp_max = max(temps) if temps else 0.0
        temp_min = min(temps) if temps else 0.0
        temp_avg = round(sum(temps) / len(temps), 1) if temps else 0.0

        # MOS FET
        charge_mos    = bool(fet_ctrl & 0x01)
        discharge_mos = bool(fet_ctrl & 0x02)

        # Ошибки
        errors = []
        for bit, info in PROTECTION_FLAGS.items():
            if prot_raw & (1 << bit):
                errors.append(info)

        # Направление и прогноз
        if current < -0.1:
            direction = "discharging"
            amps = abs(current)
            hours = (cap_rem / 100.0) / amps if amps > 0 else 0
            hrs, mins = int(hours), int((hours % 1) * 60)
            time_str = f"{hrs} ч {mins} мин"
            time_min = hrs * 60 + mins
        elif current > 0.1:
            direction = "charging"
            need_ah = (cap_tot - cap_rem) / 100.0
            hours = need_ah / current if current > 0 else 0
            hrs, mins = int(hours), int((hours % 1) * 60)
            time_str = f"до 100%: {hrs} ч {mins} мин"
            time_min = hrs * 60 + mins
        else:
            direction = "idle"
            time_str = "В покое"
            time_min = 0

        # Дата производства (если записана — формат BMS: YYMMDD packed)
        if prod_date > 0:
            year  = 2000 + (prod_date >> 9)
            month = (prod_date >> 5) & 0x0F
            day   = prod_date & 0x1F
            date_str = f"{day:02d}.{month:02d}.{year}"
        else:
            date_str = "—"

        # Энергия
        energy_rem = round(voltage * (cap_rem / 100.0), 1)
        energy_tot = round(voltage * (cap_tot / 100.0), 1)

        # Грубая оценка SoH
        soh = round(min(100.0, max(0.0, 100.0 * (1.0 - cycles / 3500.0))), 1)

        # Баланс
        balance_list = []
        for i in range(num_cells):
            balance_list.append(bool(balance_bits & (1 << i)))

        # Аптайм
        uptime_sec = time.time() - self._start_time
        if uptime_sec < 3600:
            uptime_str = f"{int(uptime_sec / 60)} мин"
        else:
            uptime_str = f"{int(uptime_sec / 3600)} ч {int((uptime_sec % 3600) / 60)} мин"

        # ─── Обновление данных ──────
        now = time.time()
        self._h_ts.append(now)
        self._h_v.append(voltage)
        self._h_i.append(current)
        self._h_p.append(power if direction == "discharging" else -power if direction == "charging" else 0)
        self._h_soc.append(soc)
        self._h_temp.append(temp_max)

        self.data.update({
            "voltage": voltage,
            "current": current,
            "power": power,
            "soc": soc,
            "capacity_remain": round(cap_rem / 100.0, 2),
            "capacity_total": round(cap_tot / 100.0, 2),
            "cycles": cycles,
            "num_cells": num_cells,
            "direction": direction,
            "time_remaining_str": time_str,
            "time_remaining_min": time_min,
            "temps": temps,
            "temp_max": temp_max,
            "temp_min": temp_min,
            "temp_avg": temp_avg,
            "balance_status": balance_list,
            "charge_mosfet": charge_mos,
            "discharge_mosfet": discharge_mos,
            "protection_raw": prot_raw,
            "errors": errors,
            "has_errors": len(errors) > 0,
            "sw_version": sw_ver,
            "production_date": date_str,
            "energy_remain_wh": energy_rem,
            "energy_total_wh": energy_tot,
            "soh_estimate": soh,
            "status": "Online",
            "last_update": now,
            "uptime_str": uptime_str,
            # Конвертация deque → list для JSON
            "history_ts":      list(self._h_ts),
            "history_voltage":  list(self._h_v),
            "history_current":  list(self._h_i),
            "history_power":    list(self._h_p),
            "history_soc":      list(self._h_soc),
            "history_temp":     list(self._h_temp),
        })

    # ─── 0x04: Ячейки ───────────────────────────────────────
    def _parse_cells(self, p: bytes):
        n = len(p) // 2
        cells = []
        for i in range(n):
            mv = struct.unpack(">H", p[i * 2:(i + 1) * 2])[0]
            cells.append(round(mv / 1000.0, 3))
        if cells:
            self.data["cells"]         = cells
            self.data["cell_min"]      = min(cells)
            self.data["cell_max"]      = max(cells)
            self.data["cell_delta_mv"] = round((max(cells) - min(cells)) * 1000)
            self.data["cell_avg"]      = round(sum(cells) / len(cells), 3)

    # ─── 0x05: Версия железа ─────────────────────────────────
    def _parse_hwver(self, p: bytes):
        try:
            self.data["hw_version"] = p.decode("ascii", errors="replace").strip("\x00")
        except Exception:
            self.data["hw_version"] = "—"

    # ─── Цикл опроса ────────────────────────────────────────
    async def update(self):
        try:
            async with BleakClient(self.mac, timeout=10.0) as client:
                await client.start_notify(NOTIFY_UUID, self._notification_handler)

                for cmd in (CMD_BASIC, CMD_CELLS, CMD_HWVER):
                    self._event.clear()
                    await client.write_gatt_char(WRITE_UUID, cmd, response=False)
                    try:
                        await asyncio.wait_for(self._event.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        pass
                    await asyncio.sleep(0.2)

                await client.stop_notify(NOTIFY_UUID)

        except Exception as e:
            self.data["status"] = f"BLE: {str(e)[:40]}"
            self.data["connection_errors"] = self.data.get("connection_errors", 0) + 1
