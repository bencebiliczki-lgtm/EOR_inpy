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

## Célgép-kompatibilitás

- A célgép Dell OptiPlex 780, Intel Core 2 Quad Q9400 processzorral, 8 GB RAM-mal
  és Windows 10 Pro 19045 rendszerrel; részletek: `docs/target-host.md`.
- Ne vezess be AVX/AVX2-t vagy Windows 11-et igénylő függőséget vagy buildet.
- A Windows-csomagnál tartsd meg a `constraints-windows-legacy.txt` szerinti
  NumPy 1.26.4 verziót, amíg a célgépes validáció más verziót nem igazol.
- Erőforrás-igényes változtatást a célgépen is validálni kell; a fizikai I/O és a
  biztonsági felügyelet időzítését UI-, export- vagy háttérmunka nem ronthatja.
- A célgépen a `COM3` Intel AMT/SOL menedzsmentport, nem pumpaport. Lehetséges
  fizikai pumpaportok: `COM1`, `COM2`, `COM4`; a szerepkiosztást helyszínen kell
  azonosítani, nem szabad kitalálni.
- Az NI-szoftver telepítettsége nem bizonyítja az USB-6001 csatlakozását; a
  hardvert és a fizikai csatornákat felderítéssel és csak olvasási próbával kell
  ellenőrizni.

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
- `docs/target-host.md`: célgép, felismert portok és kompatibilitási korlátok.
- `docs/safety.md`: biztonsági modell.
- `docs/testing.md`: tesztstratégia.
- `docs/open-questions.md`: tisztázandó műszaki kérdések.
