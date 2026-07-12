# Codex projektutasítások

## Projektcél

Ez a repository a Miskolci Egyetem AFKI EOR mérőrendszerének Windows 10 alatt futó Python vezérlő- és adatgyűjtő alkalmazását tartalmazza.

## Kötelező technológiai döntések

- Python 3.12 vagy újabb kompatibilis Python 3 verzió.
- PySide6 kezelőfelület és pyqtgraph élő diagramok.
- pyserial az ISCO soros kommunikációhoz.
- NI-DAQmx Python API az NI USB-6001 kezeléséhez.
- SQLite a projektekhez és beállításokhoz; CSV a nyers mérési adatokhoz.
- pytest, Ruff és mypy az ellenőrzésekhez; PyInstaller a Windows-csomaghoz.

## Biztonsági szabályok

- A biztonsági felügyelet minden esetben elsőbbséget élvez a PID-del és a kezelői paranccsal szemben.
- A köpenypumpa nyomása üzemi mérés közben legalább 20 barral legyen magasabb a besajtolási nyomásnál.
- Kapcsolatvesztés, érvénytelen szenzoradat vagy határérték-túllépés biztonságos állapotot váltson ki.
- Valódi hardverre író műveletet csak explicit hardveres módban és külön kezelői megerősítéssel szabad engedélyezni.
- Teszt és CI nem küldhet valódi pumpa-, szelep- vagy NI kimeneti parancsot.
- A szoftveres védelem nem helyettesíti a fizikai vészleállítást és a készülék saját nyomáshatárait.
- A biztonsági feltételek lazítását ne végezd el hallgatólagosan; jelezd és kérj jóváhagyást.

## Fejlesztési elvek

- A hardvereket Protocol/interfész mögé kell helyezni, és minden hardverhez szimulátor szükséges.
- A UI nem kommunikálhat közvetlenül a driverekkel; alkalmazási szolgáltatáson keresztül tegye.
- A fizikai I/O és az adatmentés ne blokkolja a UI szálát.
- Minden mérési rekord kapjon UTC időbélyeget, monotonic időreferenciát és minőségi/állapotjelzőt.
- A nyers mérést meg kell őrizni; a megjelenítési vagy exportformátum nem lehet az egyetlen adatforrás.
- Új viselkedéshez teszt tartozzon. Hibajavításnál először reprodukáló teszt készüljön.
- Ismeretlen DASNET-részletet ne találj ki; jelöld TODO-ként, és hivatkozz a gyártói kézikönyvre.

## Ellenőrzés

Módosítás után futtasd:

```bash
ruff check .
mypy src
pytest
```

A munka akkor kész, ha az érintett tesztek sikeresek, a dokumentáció követi a viselkedést, és a biztonsági alapértékek nem gyengültek.

## Fontos dokumentumok

- `docs/requirements.md`: funkcionális követelmények.
- `docs/architecture.md`: komponensek és adatáramlás.
- `docs/hardware.md`: eszközök és interfészek.
- `docs/safety.md`: biztonsági modell.
- `docs/testing.md`: tesztstratégia.
- `docs/open-questions.md`: tisztázandó műszaki kérdések.

