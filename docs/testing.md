# Tesztstratégia

## Automatikus tesztek

- kalibrációs képletek és tartományhiba;
- mérési modellek és állapotátmenetek;
- DASNET keretképzés és válaszfeldolgozás rögzített mintákból;
- pumpa- és NI-szimulátorok;
- összes biztonsági interlock;
- PID korlátozás, anti-windup és kézi/automata váltás;
- CSV séma, időbélyeg és tizedesformátum;
- NAS-kiesés és helyi várólista;
- konfiguráció migrációja.

## Hardveres smoke test

Felügyelt környezetben, nyomásfelépítés nélkül vagy meghatározott biztonságos tesztállapotban:

1. COM-portok és pumpaazonosítók felismerése.
2. Mindkét pumpa legalább 60 perces stabil státuszlekérése.
3. NI nyers feszültségek és kalibrált értékek összevetése referenciajellel.
4. Szelep kézi jelének ellenőrzése engedélyezett tartományban.
5. Kábelkihúzás és timeout biztonságos kezelése.
6. Helyi mentés és NAS-kiesés tesztje.

## Kiadási kapu

- Ruff, mypy és pytest sikeres.
- Biztonsági tesztek sikeresek.
- Windows build elkészül.
- Felügyelt hardveres teszt jegyzőkönyve elfogadott.
- Telepítés manuálisan jóváhagyott; automatikus üzemi telepítés nincs.

