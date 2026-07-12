# Architektúra

## Rétegek

1. **UI:** állapotmegjelenítés, projektkezelés, diagramok és kezelői parancsok.
2. **Alkalmazási szolgáltatások:** mérési munkamenet, parancsengedélyezés, állapotgép és koordináció.
3. **Biztonsági felügyelet:** határértékek, watchdog, interlockok és biztonságos állapot.
4. **Szabályozás:** PID és kézi szelepjel előállítása a biztonsági korlátozásokon belül.
5. **Eszközadapterek:** ISCO/DASNET, NI-DAQmx és ezek szimulátorai.
6. **Adatkezelés:** lokális napló, SQLite metaadatok, CSV/Excel export és NAS-szinkron.

## Függőségi szabály

A felső réteg ismerheti az alatta lévő absztrakciót, de a hardveradapter nem ismerheti a UI-t. A biztonsági felügyelet képes felülírni minden normál vezérlési kimenetet.

## Fő absztrakciók

- `Pump`: csatlakozás, státuszlekérés, felügyelt parancs és leállítás.
- `DataAcquisition`: analóg bemenetek és kimenetek kezelése.
- `SafetyMonitor`: mérési pillanatkép kiértékelése.
- `MeasurementWriter`: nyers rekord tartós mentése.
- `Clock`: valós és tesztidő leválasztása.

## Folyamat

1. Eszközadatok párhuzamos lekérése.
2. Érvényesség és frissesség ellenőrzése.
3. Kalibrált mérési pillanatkép összeállítása.
4. Biztonsági kiértékelés.
5. Veszély esetén biztonságos állapot; egyébként kézi/PID kimenet korlátozása.
6. Rekord helyi tartósítása.
7. UI frissítése és háttérben NAS-szinkron.

## Szálkezelés

A hardver I/O, adatmentés és NAS-művelet nem futhat a Qt főszálán. A szabályozási ciklusnak mérhető határideje és watchdogja legyen. A konkrét concurrency-modell az első hardverprototípus mérései után véglegesítendő.

