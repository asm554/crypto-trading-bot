"""Bot 2 - Cartographer: bearische 4H-FVGs ueber den ganzen Impuls (Anker -> aktuelles Tief), 1H-Bestaetigung."""
from dataclasses import dataclass

from .candles import C


@dataclass(frozen=True)
class Zone:
    top: float
    bottom: float
    formed_ts: int
    status: str  # UNTESTED | TESTED | FILLED

    @property
    def key(self) -> str:
        return f"{self.bottom:.8g}-{self.top:.8g}"


def find_fvgs(c4h: list[C], anchor_ts: int) -> list[Zone]:
    """Bearisches FVG: Low von Kerze a liegt ueber High von Kerze c (a,b,c aufeinanderfolgend).
    TESTED: spaeterer Docht erreicht die Unterkante (auch teilweise). FILLED: 4H-Close ueber Oberkante."""
    zones = []
    for k in range(2, len(c4h)):
        a, c = c4h[k - 2], c4h[k]
        if a.ts < anchor_ts or not (a.l > c.h):
            continue
        status = "UNTESTED"
        for later in c4h[k + 1:]:
            if later.c > a.l:
                status = "FILLED"
                break
            if later.h >= c.h:
                status = "TESTED"
        zones.append(Zone(top=a.l, bottom=c.h, formed_ts=c.ts, status=status))
    return sorted(zones, key=lambda z: -z.top)  # Leiter von oben nach unten


def confirm_1h(zone: Zone, c1h: list[C]) -> dict | None:
    """Letzte geschlossene 1H-Kerze drang in die Zone ein, schloss rot und unter der Unterkante."""
    if zone.status == "FILLED" or not c1h:
        return None
    last = c1h[-1]
    if last.ts > zone.formed_ts and last.h >= zone.bottom and last.c < zone.bottom and last.c < last.o:
        return {"entry": last.c, "confirm_ts": last.ts}
    return None
